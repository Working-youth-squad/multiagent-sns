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
from typing import cast

from sns.render.code_image import MAX_CODE_LINES
from sns.render.text import display_width

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
# 스톡 검색어 길이 상한. 길어질수록 결과가 0건으로 수렴해 그라데이션 폴백만 늘어난다.
MAX_IMAGE_QUERY_LEN = 60

DEFAULT_VOICE = "ko-KR-Chirp3-HD-Charon"
# 코드가 없는 컷의 정사각을 채우는 그라데이션 + 텍스트/액센트 (다크 브랜드 팔레트).
DEFAULT_BACKGROUND = "#0d1117"
DEFAULT_BACKGROUND2 = "#1b2a4a"
DEFAULT_FOREGROUND = "#e6edf3"
DEFAULT_ACCENT = "#58a6ff"


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
    image_query: str = ""  # 스톡 검색어(영문). 생성 시점에 image_ref로 해소된다
    image_ref: str = ""  # 해소된 사진의 저장소 URL. 렌더러가 읽는 건 이쪽뿐이다


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
    accent: str = DEFAULT_ACCENT
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


def _parse_image_query(raw: Mapping[str, object], where: str, code: str) -> str:
    """스톡 검색어. 영문·짧게만 받는다 — 게이트와 검색이 모두 영어 기준이다."""
    query = _optional_str(raw, "image_query", where).strip()
    if not query:
        return ""
    if code.strip():
        raise VideoSpecError(
            f"{where}'image_query'와 'code'는 함께 쓸 수 없음 — 정사각은 하나뿐이다"
        )
    if not query.isascii():
        raise VideoSpecError(f"{where}'image_query'는 영문이어야 함(금지어 판정 기준): {query!r}")
    if len(query) > MAX_IMAGE_QUERY_LEN:
        raise VideoSpecError(
            f"{where}'image_query'는 {MAX_IMAGE_QUERY_LEN}자 이하여야 함: {len(query)}자"
        )
    return query


def _parse_slide(raw: object, index: int) -> Slide:
    where = f"'slides[{index}]'의 "
    if not isinstance(raw, Mapping):
        raise VideoSpecError(f"'slides[{index}]'는 매핑이어야 함: {raw!r}")
    code = _optional_str(raw, "code", where)
    if code.strip() and len(code.rstrip().split("\n")) > MAX_CODE_LINES:
        raise VideoSpecError(
            f"{where}'code'는 {MAX_CODE_LINES}줄 이하여야 함: {len(code.rstrip().split(chr(10)))}줄"
        )
    return Slide(
        subtitle=_require_text(raw, "subtitle", where, MAX_SUBTITLE_WIDTH),
        narration=_require_text(raw, "narration", where, MAX_NARRATION_WIDTH),
        code=code,
        lang=_optional_str(raw, "lang", where),
        focus_lines=_parse_focus(raw, where, code),
        image_query=_parse_image_query(raw, where, code),
        image_ref=_optional_str(raw, "image_ref", where),
    )


def _parse_slides(spec: Mapping[str, object]) -> tuple[Slide, ...]:
    value = spec.get("slides")
    if not isinstance(value, list) or not value:
        raise VideoSpecError(f"'slides'는 비지 않은 리스트여야 함: {value!r}")
    if len(value) > MAX_SLIDES:
        raise VideoSpecError(f"'slides'는 {MAX_SLIDES}장 이하여야 함: {len(value)}장")
    return tuple(_parse_slide(raw, i) for i, raw in enumerate(value))


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


def parse_video_spec(media_spec: Mapping[str, object]) -> VideoSpec:
    """`media_spec` → `VideoSpec`. 누락·형식 오류는 `VideoSpecError`."""
    width = _parse_dimension(media_spec, "width", DEFAULT_WIDTH)
    height = _parse_dimension(media_spec, "height", DEFAULT_HEIGHT)
    if width * 16 != height * 9:
        raise VideoSpecError(f"쇼츠/릴스는 세로 9:16이어야 함: {width}×{height}")
    return VideoSpec(
        width=width,
        height=height,
        topic=_require_text(media_spec, "topic", "", MAX_TOPIC_WIDTH),
        slides=_parse_slides(media_spec),
        voice=_parse_voice(media_spec),
        background=_parse_color(media_spec, "background", DEFAULT_BACKGROUND),
        background2=_parse_color(media_spec, "background2", DEFAULT_BACKGROUND2),
        foreground=_parse_color(media_spec, "foreground", DEFAULT_FOREGROUND),
        accent=_parse_color(media_spec, "accent", DEFAULT_ACCENT),
    )
