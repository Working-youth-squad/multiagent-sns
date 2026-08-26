"""영상 렌더 입력 스펙 — `media_spec`(jsonb) → 검증된 `VideoSpec` (FR-G2·M2).

카드 spec(`sns.render.card.spec`)과 같은 방어선: Content Agent(LLM) 산출물을 결정론
렌더의 확정 입력으로 파싱하고, 누락·타입 오류·환각 치수는 렌더 진입 전에
`VideoSpecError`로 끊는다.

3단 레이아웃 기준 구조 (1080×1920, 검은 바탕):

       0 ~  360   부제 알약(컷마다 변화) + **주제**(영상 내내 고정)
     360 ~ 1300   정사각 940 — 코드 이미지 → 주제 사진 → 그라데이션 순으로 채운다
    1300 ~ 1920   자막 = 나레이션. 쇼츠 UI 가림 영역을 피해 위쪽부터 채운다

`topic`이 영상 내내 고정되는 앵커다. 예전에는 슬라이드마다 제목이 바뀌어 "무슨 영상인지"를
잡아주는 게 없었다. 바뀌는 것은 부제·코드 초점·자막이고, 주제는 남는다.

**슬라이드 1장 = 컷 1개 = 화면 1장.** 예전에는 나레이션을 문장 단위로 쪼개 컷을 만들고
컷을 다시 세그먼트로 등분했는데, Ken Burns 줌을 걷어내면서 그 층이 전부 불필요해졌다
(줌이 없으면 같은 컷의 세그먼트는 완전히 동일한 프레임이다). 대신 나레이션이 길면
쪼개주지 않고 **거부한다** — 한 컷이 곧 한 화면이라 길면 그 화면이 오래 정지한다.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, cast

from sns.render.concept_image import Concept, ConceptError, parse_concept
from sns.render.text import display_width
from sns.tools.contracts import VideoMethod
from sns.topic_policy import concept_kinds_for, square_sources_for

# 쇼츠/릴스 세로 규격 9:16 (FR-M2). spec이 명시하면 덮어쓰되 비율은 강제.
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920
# 변당 최대 치수 — 카드 spec의 MAX_CARD_SIDE와 같은 메모리 폭탄 방어.
MAX_SIDE = 4096
MAX_SLIDES = 60

# 표시 폭 상한 (한글 1자 = 2). 전부 실측이다 — 맑은고딕, 상단 80px·알약 38px 기준.
#   주제 : 한글 22자까지 상단에서 2줄. 24자면 3줄이 되어 알약을 밀어낸다.
#   부제 : 한글 20자면 알약 828px. 그 이상은 화면 폭(940)을 넘는다.
MAX_TOPIC_WIDTH = 44
MAX_SUBTITLE_WIDTH = 40
# 컷 1개의 나레이션 폭 상한. Chirp 3 HD 한국어 실측 8.0자/초 → 한글 31자면 약 3.9초로
# FR-A2의 화면 전환 주기(2~4초) 안에 들어온다. 한 컷 = 한 화면이라 이게 곧 정지 시간이다.
MAX_NARRATION_WIDTH = 62
# 스톡 검색어 상한 — 길수록 결과가 0건으로 수렴한다(검색은 짧아야 걸린다).
# 정사각 소스 → 그 소스가 쓰는 슬라이드 필드. **파서와 렌더러의 단일 출처**다 —
# 따로 두면 한쪽만 갱신돼 "프롬프트는 막았는데 파서는 받는" 상태가 다시 생긴다.
# 코드 줄 상한 — [sns.render.code_image]의 렌더 한계와 같은 값이다. 여기 복제해 두는 건
# spec이 그 모듈(과 pygments)을 물지 않게 하기 위해서다. 어긋나면 아래 테스트가 잡는다.
MAX_CODE_LINES = 18

SQUARE_FIELDS: dict[str, tuple[str, ...]] = {
    "code": ("code", "lang", "focus_lines"),
    "concept": ("concept",),
    "image": ("image_query", "image_prompt", "image_ref"),
    "gradient": (),  # 최후 폴백 — 슬라이드 필드가 없다
}

# 해소 전(PLAN)과 해소 후(RENDER)는 필수 필드가 다르다. `parse_video_spec`이 두 시점에
# 불리므로(set_media_spec · 렌더 직전) 평평한 허용 집합으로는 그 차이를 표현할 수 없다.
SpecStage = Literal["plan", "render"]

# 해소 실패가 기록되는 자리. 이 값이 있으면 그 컷은 RENDER 필수 필드를 면제받고
# 폴백(그라데이션)으로 간다 — 실패 기록이 폴백을 승인하는 티켓이다.
FAILURE_FIELD = "scene_failure"


@dataclass(frozen=True)
class MethodFields:
    """method별 슬라이드 필드 규칙."""

    allowed: tuple[str, ...]
    required_plan: tuple[str, ...]
    required_render: tuple[str, ...]
    resolved: tuple[str, ...]
    """해소가 채우는 필드 → **PLAN 단계에서 금지**한다.

    앞의 셋만으로는 이걸 표현할 수 없다. `required_render - required_plan`으로 파생시키려
    해도 template의 `image_ref`는 렌더에서 필수가 아니라(그라데이션 폴백) 그 뺄셈에서
    빠져나가고, LLM이 가짜 `image_ref`를 써넣는 경로가 열린 채로 남는다.
    """


METHOD_FIELDS: dict[VideoMethod, MethodFields] = {
    "template": MethodFields(
        allowed=(
            "code",
            "lang",
            "focus_lines",
            "concept",
            "image_query",
            "image_prompt",
            "image_ref",
        ),
        required_plan=(),  # 정사각은 비워도 그라데이션으로 간다
        required_render=(),
        resolved=("image_ref",),
    ),
    "generated_scene": MethodFields(
        allowed=("scene_prompt", "scene_ref", FAILURE_FIELD),
        required_plan=("scene_prompt",),
        required_render=("scene_ref",),
        resolved=("scene_ref", FAILURE_FIELD),
    ),
}
# hybrid는 여기 항목을 두지 않는다 — 합집합으로 검증하면 아무 필드나 섞여도 통과해
# 자물쇠가 풀린다. 검증 단위를 컷으로 내리면 합집합이라는 개념 자체가 사라진다.

MAX_IMAGE_QUERY_LEN = 60
# 생성 프롬프트 상한 — 근거가 반대다. 구도를 설명해야 하므로 문장 하나는 들어가야 하고,
# 검색어 길이(60)를 물려 뒀더니 "무엇이 어떻게 놓였는지"를 쓸 수 없었다. 다만 무한정
# 길면 고정 화풍([sns.render.images.generate.STYLE_RULES])과 싸우기 시작한다.
MAX_IMAGE_PROMPT_LEN = 200

DEFAULT_VOICE = "ko-KR-Chirp3-HD-Charon"
# 코드가 없는 컷의 정사각을 채우는 그라데이션 + 텍스트/액센트 (화이트 모드 기본).
# 다크 팔레트는 classic 템플릿([sns.render.video.classic.spec])에 보존돼 있다.
DEFAULT_BACKGROUND = "#ffffff"
DEFAULT_BACKGROUND2 = "#dbe4f0"
DEFAULT_FOREGROUND = "#1f2328"
DEFAULT_ACCENT = "#0969da"


class VideoSpecError(ValueError):
    """malformed `media_spec` — 렌더 진입 전 차단."""


@dataclass(frozen=True)
class Slide:
    """화면 1장 = 컷 1개. 부제·코드·초점·나레이션이 컷마다 바뀐다."""

    subtitle: str  # 상단 알약 — 이 컷이 무엇을 다루는지
    narration: str  # TTS 발화이자 하단 자막
    code: str = ""  # 정사각에 넣을 코드. 비면 사진, 그것도 없으면 그라데이션
    lang: str = ""  # pygments 렉서 이름 (비면 추측)
    focus_lines: tuple[int, ...] = ()  # 밝게 둘 코드 줄(1-기반). 나머지는 눌린다
    concept: Concept | None = None  # 우리가 그리는 개념 그림(강조·도해·기억)
    image_query: str = ""  # 스톡 검색어(영문). 생성 시점에 image_ref로 해소된다
    image_prompt: str = ""  # 생성 이미지 주제(영문). 유료라 기본 미배선
    image_ref: str = ""  # 해소된 사진의 저장소 URL. 렌더러가 읽는 건 이쪽뿐이다
    # 컷의 제작 방식. spec.method가 hybrid가 아니면 코드가 spec.method로 채운다 —
    # **검증 단위는 언제나 이쪽**이라 hybrid가 특별한 분기를 안 만든다.
    method: VideoMethod = "template"
    scene_prompt: str = ""  # 생성할 장면(영문 한 문장). 해소 시점에 scene_ref가 된다
    scene_ref: str = ""  # 해소된 장면의 저장소 URL. 렌더러가 읽는 건 이쪽뿐이다
    scene_failed: bool = False  # 해소가 실패해 폴백으로 가는 컷(사유는 media_spec에)


@dataclass(frozen=True)
class VideoSpec:
    width: int
    height: int
    topic: str  # 영상 내내 고정되는 주제 — 시청자의 앵커
    slides: tuple[Slide, ...]
    voice: str
    background: str
    background2: str
    foreground: str
    # 렌더러는 주제 대분류를 모른다 — 파서가 채우기 우선순위를 여기 실어 보낸다
    # ([sns.render.video.renderer]가 이 순서대로 첫 번째로 채워지는 소스를 쓴다).
    # 같은 media_spec + 같은 topic_major → 같은 VideoSpec → 같은 mp4(FR-M1 결정론).
    # **기본값을 두지 않는다** — 빠뜨리면 요리 채널에 개발 순서가 조용히 적용된다.
    square_sources: tuple[str, ...]
    accent: str = DEFAULT_ACCENT
    # 채널 캐릭터의 저장소 URL. 비면 캐릭터 없이 렌더한다(인터뷰에서 "캐릭터 없음"을
    # 골랐거나 생성이 실패한 채널). **배선이 아니라 spec에 있어야 한다** — 밖에서 넘기면
    # 같은 media_spec이 채널마다 다른 mp4를 낳아 FR-M1이 깨진다. `image_ref`와 같은 규율로
    # 해소 시점에 못박고 렌더러는 이것만 읽는다([sns.render.video.mascot]). 승인 웹
    # 재렌더가 채널 조립 없이 도는 근거이기도 하다.
    character_ref: str = ""
    # **축이 둘이다.** 섞으면 "이 영상이 어떻게 만들어졌나"의 답이 둘이 되고 어긋난다.
    #   method — 어느 트랙인가(재료 출처). 비용·AI 표기·Capability Gate가 여기 걸린다.
    #   style  — 그 트랙 안의 화면 문법. spec에 있어야 승인 웹 재렌더가 같은 꼴로 돈다.
    # hybrid면 method가 컷마다 다를 수 있어 검증 단위는 slide.method다.
    method: VideoMethod = "template"
    style: str = ""
    _unused: tuple[()] = field(default=(), repr=False, compare=False)


def _require_text(raw: Mapping[str, object], key: str, where: str, max_width: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise VideoSpecError(f"{where}'{key}'는 비지 않은 문자열이어야 함: {value!r}")
    text = value.strip()
    if display_width(text) > max_width:
        raise VideoSpecError(
            f"{where}'{key}'가 화면 폭을 넘음 — 표시 폭 {max_width} 이하"
            f"(한글 {max_width // 2}자): 현재 {display_width(text)}"
        )
    return text


def _optional_str(raw: Mapping[str, object], key: str, where: str) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str):
        raise VideoSpecError(f"{where}'{key}'는 문자열이어야 함: {value!r}")
    return value


def _parse_focus(raw: Mapping[str, object], where: str, code: str) -> tuple[int, ...]:
    value = raw.get("focus_lines", ())
    if isinstance(value, str) or not isinstance(value, list | tuple):
        if value == ():
            return ()
        raise VideoSpecError(f"{where}'focus_lines'는 정수 리스트여야 함: {value!r}")
    numbers = tuple(value)
    if not numbers:
        return ()
    if not code.strip():
        raise VideoSpecError(f"{where}'focus_lines'가 있는데 'code'가 없음 — 가리킬 대상이 없다")
    line_count = len(code.rstrip().split("\n"))
    for n in numbers:
        if not isinstance(n, int) or isinstance(n, bool):
            raise VideoSpecError(f"{where}'focus_lines' 항목은 정수여야 함: {n!r}")
        if not 1 <= n <= line_count:
            raise VideoSpecError(f"{where}'focus_lines' 줄 번호가 범위(1~{line_count}) 밖: {n}")
    return cast(tuple[int, ...], numbers)


def _parse_image_text(
    raw: Mapping[str, object], key: str, where: str, code: str, max_len: int
) -> str:
    """스톡 검색어·생성 프롬프트. 영문만 받는다 — 게이트와 모델이 모두 영어 기준이다."""
    query = _optional_str(raw, key, where).strip()
    if not query:
        return ""
    if code.strip():
        raise VideoSpecError(f"{where}'{key}'와 'code'는 함께 쓸 수 없음 — 정사각은 하나뿐이다")
    if not query.isascii():
        raise VideoSpecError(f"{where}'{key}'는 영문이어야 함(금지어 판정 기준): {query!r}")
    if len(query) > max_len:
        raise VideoSpecError(f"{where}'{key}'는 {max_len}자 이하여야 함: {len(query)}자")
    return query


def _parse_concept(raw: Mapping[str, object], where: str, kinds: tuple[str, ...]) -> Concept | None:
    """개념 그림 — 검증은 [sns.render.concept_image]에 위임하고 예외만 갈아 끼운다."""
    value = raw.get("concept")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise VideoSpecError(f"{where}'concept'는 매핑이어야 함: {value!r}")
    try:
        return parse_concept(value, kinds=kinds)
    except ConceptError as exc:
        raise VideoSpecError(f"{where}'concept'가 잘못됨 — {exc}") from exc


def _reject_unused_square_fields(
    raw: Mapping[str, object], where: str, sources: tuple[str, ...]
) -> None:
    """팩이 안 쓰는 정사각 소스의 필드는 거부한다.

    프롬프트에서 안내를 빼는 것만으로는 1차 방어선뿐이다. 에이전트가 안내를 무시하거나
    옛 spec을 재사용하면 그 필드가 그대로 렌더까지 간다 — 코드를 안 쓰는 도메인의 영상에
    파이썬 코드가 나온다. 사유에 어느 소스인지 적어 에이전트가 고칠 수 있게 한다.
    """
    for source, fields in SQUARE_FIELDS.items():
        if source in sources:
            continue
        for key in fields:
            if raw.get(key):
                raise VideoSpecError(
                    f"{where}'{key}'는 이 도메인이 쓰지 않는 정사각 소스({source})다 — "
                    f"쓸 수 있는 소스: {list(sources)}"
                )


def _parse_method(value: object, where: str) -> VideoMethod:
    if value is None:
        return "template"  # method 없는 기존 media_spec은 템플릿이다
    if not isinstance(value, str) or value not in METHOD_FIELDS:
        raise VideoSpecError(f"{where}'method'는 {sorted(METHOD_FIELDS)} 중 하나여야 함: {value!r}")
    # `in METHOD_FIELDS`가 이미 VideoMethod로 좁혀준다 — cast를 쓰면 mypy가 중복이라 잡는다.
    return value


def _check_lifecycle(
    raw: Mapping[str, object], where: str, method: VideoMethod, stage: SpecStage
) -> None:
    """단계별 필드 검증 — 해소 전후로 필수가 다르다.

    PLAN에서 `resolved` 필드를 금지하는 이유는 둘이다. 해소되지 않은 spec임을 구조로
    못박고, **LLM이 저장소 URL을 환각으로 써넣는 것**을 막는다. 지어낸 URL이 통과하면
    그 컷은 남의 자산을 가리키거나 렌더에서 알 수 없는 오류로 죽는다.
    """
    rules = METHOD_FIELDS[method]
    if stage == "plan":
        for key in rules.required_plan:
            if not raw.get(key):
                raise VideoSpecError(f"{where}'{key}'가 없음 — {method}는 이 필드가 필수다")
        for key in rules.resolved:
            if raw.get(key):
                raise VideoSpecError(
                    f"{where}'{key}'는 해소가 채우는 필드다 — 생성 시점에 쓸 수 없다"
                )
        return
    for key in rules.required_render:
        # 실패가 기록된 컷은 면제한다 — 그 컷은 폴백으로 간다(실패 기록이 폴백 티켓).
        if not raw.get(key) and not raw.get(FAILURE_FIELD):
            raise VideoSpecError(
                f"{where}'{key}'가 없음 — 해소를 돌리지 않았거나 실패 기록이 빠졌다"
            )


def _reject_fields_outside_method(
    raw: Mapping[str, object], where: str, method: VideoMethod
) -> None:
    """이 method가 안 쓰는 필드는 거부한다 — 선언과 실제가 어긋나는 유일한 실패 모드다."""
    allowed = set(METHOD_FIELDS[method].allowed)
    for other, rules in METHOD_FIELDS.items():
        if other == method:
            continue
        for key in rules.allowed:
            if key not in allowed and raw.get(key):
                raise VideoSpecError(
                    f"{where}'{key}'는 {method}가 쓰지 않는 필드다({other}의 것) — "
                    f"쓸 수 있는 것: {sorted(allowed)}"
                )


def _parse_slide(
    raw: object,
    index: int,
    kinds: tuple[str, ...],
    sources: tuple[str, ...],
    method: VideoMethod,
    stage: SpecStage,
) -> Slide:
    where = f"'slides[{index}]'의 "
    if not isinstance(raw, Mapping):
        raise VideoSpecError(f"'slides[{index}]'는 매핑이어야 함: {raw!r}")
    slide_method = _parse_method(raw.get("method"), where) if method == "hybrid" else method
    _reject_fields_outside_method(raw, where, slide_method)
    _check_lifecycle(raw, where, slide_method, stage)
    if slide_method != "template":
        # 정사각 자물쇠는 template 안에서만 의미가 있다 — 풀블리드 장면엔 정사각이 없다.
        return Slide(
            subtitle=_require_text(raw, "subtitle", where, MAX_SUBTITLE_WIDTH),
            narration=_require_text(raw, "narration", where, MAX_NARRATION_WIDTH),
            method=slide_method,
            scene_prompt=_optional_str(raw, "scene_prompt", where),
            scene_ref=_optional_str(raw, "scene_ref", where),
            scene_failed=bool(raw.get(FAILURE_FIELD)),
        )
    _reject_unused_square_fields(raw, where, sources)
    code = _optional_str(raw, "code", where)
    if code.strip() and len(code.rstrip().split("\n")) > MAX_CODE_LINES:
        raise VideoSpecError(
            f"{where}'code'는 {MAX_CODE_LINES}줄 이하여야 함: {len(code.rstrip().split(chr(10)))}줄"
        )
    concept = _parse_concept(raw, where, kinds)
    if concept is not None and (code.strip() or raw.get("image_query") or raw.get("image_prompt")):
        raise VideoSpecError(
            f"{where}'concept'는 'code'·'image_query'·'image_prompt'와 함께 쓸 수 없음"
        )
    return Slide(
        subtitle=_require_text(raw, "subtitle", where, MAX_SUBTITLE_WIDTH),
        narration=_require_text(raw, "narration", where, MAX_NARRATION_WIDTH),
        code=code,
        lang=_optional_str(raw, "lang", where),
        focus_lines=_parse_focus(raw, where, code),
        concept=concept,
        image_query=_parse_image_text(raw, "image_query", where, code, MAX_IMAGE_QUERY_LEN),
        image_prompt=_parse_image_text(raw, "image_prompt", where, code, MAX_IMAGE_PROMPT_LEN),
        image_ref=_optional_str(raw, "image_ref", where),
        method=slide_method,
    )


def _reject_generated_images_in_code_videos(slides: tuple[Slide, ...]) -> None:
    """코드가 한 컷이라도 있으면 `image_prompt`를 영상 전체에서 거부한다.

    컷 하나만 봐서는 판정할 수 없어 여기(영상 단위)에 있다. 근거는 실측이다 —
    같은 영상에서 컷 둘만 gpt-image-1로 바꿔 나란히 놓고 골랐는데, 코드를 다루는 영상은
    핵심 컷이 대개 숫자와 비교였다. "101번 → 2번"을 개념 그림은 글자로 쓰지만 생성
    이미지는 화살표 개수를 세게 만든다. **코드를 보여주는 순간 그 영상은 코드 영상이다.**

    실사 사진(`image_query`)은 이 규칙 밖이다 — 막은 건 생성 이미지고, 근거가 다르다.
    """
    code_cuts = [i for i, s in enumerate(slides) if s.code.strip()]
    prompt_cuts = [i for i, s in enumerate(slides) if s.image_prompt]
    if code_cuts and prompt_cuts:
        raise VideoSpecError(
            "코드가 있는 영상에서는 'image_prompt'를 쓸 수 없음 — 개념 그림(concept)을 쓰세요. "
            f"코드: {', '.join(f'slides[{i}]' for i in code_cuts)} / "
            f"생성 요청: {', '.join(f'slides[{i}]' for i in prompt_cuts)}"
        )


def _parse_slides(
    spec: Mapping[str, object],
    kinds: tuple[str, ...],
    sources: tuple[str, ...],
    method: VideoMethod,
    stage: SpecStage,
) -> tuple[Slide, ...]:
    value = spec.get("slides")
    if not isinstance(value, list) or not value:
        raise VideoSpecError(f"'slides'는 비지 않은 리스트여야 함: {value!r}")
    if len(value) > MAX_SLIDES:
        raise VideoSpecError(f"'slides'는 {MAX_SLIDES}장 이하여야 함: {len(value)}장")
    slides = tuple(
        _parse_slide(raw, i, kinds, sources, method, stage) for i, raw in enumerate(value)
    )
    if "code" in sources and method == "template":
        # 코드를 안 쓰는 도메인엔 "코드 영상"이라는 개념 자체가 없다.
        _reject_generated_images_in_code_videos(slides)
    return slides


def _valid_hex(color: str) -> bool:
    if not (len(color) == 7 and color[0] == "#"):
        return False
    try:
        int(color[1:], 16)
    except ValueError:
        return False
    return True


def _parse_color(spec: Mapping[str, object], key: str, default: str) -> str:
    value = spec.get(key, default)
    if not isinstance(value, str) or not _valid_hex(value):
        raise VideoSpecError(f"'{key}'는 '#RRGGBB' 형식이어야 함: {value!r}")
    return value.lower()


def _parse_dimension(spec: Mapping[str, object], key: str, default: int) -> int:
    value = spec.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise VideoSpecError(f"'{key}'는 양의 정수여야 함: {value!r}")
    if value > MAX_SIDE:
        raise VideoSpecError(f"'{key}'는 {MAX_SIDE}px 이하여야 함(메모리 폭탄 방어): {value!r}")
    return value


def _parse_voice(spec: Mapping[str, object]) -> str:
    value = spec.get("voice", DEFAULT_VOICE)
    if not isinstance(value, str) or not value.strip():
        raise VideoSpecError(f"'voice'는 비지 않은 문자열이어야 함: {value!r}")
    return value


# 화면 문법 — `method`와 직교한다([sns.render.video.media]의 `_RENDERERS`).
VIDEO_STYLES = ("", "motion")


def _parse_style(spec: Mapping[str, object]) -> str:
    value = _optional_str(spec, "style", "")
    if value not in VIDEO_STYLES:
        raise VideoSpecError(f"'style'은 {VIDEO_STYLES} 중 하나여야 함: {value!r}")
    return value


def parse_video_spec(
    media_spec: Mapping[str, object], *, topic_major: str, stage: SpecStage = "render"
) -> VideoSpec:
    """`media_spec` → `VideoSpec`. 누락·형식 오류는 `VideoSpecError`.

    `topic_major`는 **필수다.** 기본값을 두면 새 호출부가 인자를 빠뜨렸을 때 요리 채널에
    개발 규칙(코드 정사각·terminal 그림)이 조용히 적용된다 — 그 사고는 렌더가 끝난 뒤에야
    드러난다. 하위 호환은 호출부가 `DEV_MAJOR`를 명시해서 얻는다([sns.topic_policy]).
    """
    width = _parse_dimension(media_spec, "width", DEFAULT_WIDTH)
    height = _parse_dimension(media_spec, "height", DEFAULT_HEIGHT)
    if width * 16 != height * 9:
        raise VideoSpecError(f"쇼츠/릴스는 세로 9:16이어야 함: {width}×{height}")
    sources = square_sources_for(topic_major)
    method = _parse_method(media_spec.get("method"), "")
    return VideoSpec(
        width=width,
        height=height,
        topic=_require_text(media_spec, "topic", "", MAX_TOPIC_WIDTH),
        slides=_parse_slides(media_spec, concept_kinds_for(topic_major), sources, method, stage),
        voice=_parse_voice(media_spec),
        background=_parse_color(media_spec, "background", DEFAULT_BACKGROUND),
        background2=_parse_color(media_spec, "background2", DEFAULT_BACKGROUND2),
        foreground=_parse_color(media_spec, "foreground", DEFAULT_FOREGROUND),
        accent=_parse_color(media_spec, "accent", DEFAULT_ACCENT),
        square_sources=sources,
        character_ref=_optional_str(media_spec, "character_ref", ""),
        method=method,
        style=_parse_style(media_spec),
    )
