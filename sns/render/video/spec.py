"""영상 렌더 입력 스펙 — `media_spec`(jsonb) → 검증된 `VideoSpec` (FR-G2·M2).

카드 spec(`sns.render.card.spec`)과 같은 방어선: Content Agent(LLM) 산출물을
결정론 렌더의 확정 입력으로 파싱하고, 누락·타입 오류·환각 치수는 렌더 진입 전에
`VideoSpecError`로 끊는다.

슬라이드 구조(FR-G2 — 장면·자막·나레이션 분리):
- `title`: 화면 대형 텍스트 (짧은 훅/요점)
- `body`: 화면 부연 텍스트 (선택)
- `narration`: TTS 발화문 (비우면 title+body) — 화면과 다른, 말로 풀어쓴 문장 권장
하위 호환: 슬라이드가 문자열이면 title=narration=그 문자열.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from sns.render.text import display_width, split_sentences

# 쇼츠/릴스 세로 규격 9:16 (FR-M2). spec이 명시하면 덮어쓰되 비율은 강제.
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920
# 변당 최대 치수 — 카드 spec의 MAX_CARD_SIDE와 같은 메모리 폭탄 방어.
MAX_SIDE = 4096
# 슬라이드 수 상한: 쇼츠 최대 180s ÷ 화면 전환 최소 2~4s(FR-A2) 감안한 넉넉한 값.
MAX_SLIDES = 60
# 컷 1개(=문장 1개)의 나레이션 표시 폭 상한.
# Chirp 3 HD 한국어 실측 발화 속도 8.0자/초 → 한글 31자(폭 62)면 약 3.9초로,
# FR-A2가 요구하는 화면 전환 주기 2~4초 안에 들어온다. 문장이 이보다 길면 그 컷의
# 화면이 4초 넘게 정지하므로 파싱에서 끊는다 — 나레이션 **전체** 길이는 제한하지
# 않는다(문장으로 쪼개면 정보량을 깎지 않고도 전환 주기를 지킬 수 있다).
MAX_NARRATION_WIDTH = 62

DEFAULT_VOICE = "ko-KR-Chirp3-HD-Charon"
# 세로 그라데이션 배경 (위 → 아래) + 텍스트/액센트 — 다크 브랜드 팔레트.
DEFAULT_BACKGROUND = "#0d1117"
DEFAULT_BACKGROUND2 = "#1b2a4a"
DEFAULT_FOREGROUND = "#e6edf3"
DEFAULT_ACCENT = "#58a6ff"


class VideoSpecError(ValueError):
    """malformed `media_spec` — 렌더 진입 전 차단."""


@dataclass(frozen=True)
class Slide:
    title: str  # 화면 대형 텍스트
    body: str = ""  # 화면 부연 (선택)
    narration: str = ""  # TTS 발화 — 비면 title+body

    @property
    def narration_text(self) -> str:
        return self.narration or f"{self.title} {self.body}".strip()


@dataclass(frozen=True)
class Cut:
    """화면 전환 단위 = 나레이션 문장 1개.

    렌더러의 세그먼트 1개에 대응한다. 같은 슬라이드에서 나온 컷들은 같은 화면 요소
    (title·body)를 쓰지만 각자 세그먼트라 줌 방향이 교차하고 자막이 바뀐다.
    """

    slide: "Slide"
    text: str  # 이 컷의 나레이션 문장 (TTS 입력이자 자막)


@dataclass(frozen=True)
class VideoSpec:
    width: int
    height: int
    slides: tuple[Slide, ...]
    voice: str  # TTS 보이스 이름 (예: ko-KR-Chirp3-HD-Charon)
    background: str  # 그라데이션 상단 "#RRGGBB"
    background2: str  # 그라데이션 하단
    foreground: str
    accent: str  # 제목 장식·진행바 색

    @property
    def cuts(self) -> tuple[Cut, ...]:
        """전 슬라이드를 문장 단위 컷으로 펼친다 — 렌더러의 실제 반복 단위."""
        return tuple(
            Cut(slide=slide, text=sentence)
            for slide in self.slides
            for sentence in split_sentences(slide.narration_text)
        )


def _valid_hex(color: str) -> bool:
    if not (len(color) == 7 and color[0] == "#"):
        return False
    try:
        int(color[1:], 16)
    except ValueError:
        return False
    return True


def _parse_dimension(spec: Mapping[str, object], key: str, default: int) -> int:
    value = spec.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise VideoSpecError(f"'{key}'는 양의 정수여야 함: {value!r}")
    if value > MAX_SIDE:
        raise VideoSpecError(f"'{key}'는 {MAX_SIDE}px 이하여야 함(메모리 폭탄 방어): {value!r}")
    return value


def _slide_str(raw: Mapping[str, object], key: str, i: int, *, required: bool) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise VideoSpecError(
            f"'slides[{i}].{key}'는 {'비지 않은 ' if required else ''}문자열이어야 함: {value!r}"
        )
    return value.strip()


def _parse_slides(spec: Mapping[str, object]) -> tuple[Slide, ...]:
    value = spec.get("slides")
    if not isinstance(value, list) or not value:
        raise VideoSpecError(f"'slides'는 비지 않은 리스트여야 함: {value!r}")
    if len(value) > MAX_SLIDES:
        raise VideoSpecError(f"'slides'는 {MAX_SLIDES}장 이하여야 함: {len(value)}장")
    slides: list[Slide] = []
    for i, raw in enumerate(value):
        if isinstance(raw, str):  # 하위 호환: 문자열 = 제목이자 나레이션
            if not raw.strip():
                raise VideoSpecError(f"'slides[{i}]'는 비지 않은 문자열이어야 함: {raw!r}")
            slides.append(Slide(title=raw.strip()))
        elif isinstance(raw, Mapping):
            slides.append(
                Slide(
                    title=_slide_str(raw, "title", i, required=True),
                    body=_slide_str(raw, "body", i, required=False),
                    narration=_slide_str(raw, "narration", i, required=False),
                )
            )
        else:
            raise VideoSpecError(f"'slides[{i}]'는 문자열 또는 매핑이어야 함: {raw!r}")
        _check_narration_cuts(slides[-1].narration_text, i)
    return tuple(slides)


def _check_narration_cuts(narration: str, i: int) -> None:
    """문장 하나하나가 컷이 되므로, **문장별**로 폭 상한을 강제한다."""
    for j, sentence in enumerate(split_sentences(narration)):
        if display_width(sentence) > MAX_NARRATION_WIDTH:
            raise VideoSpecError(
                f"'slides[{i}].narration'의 {j + 1}번째 문장이 한 컷에 담기엔 김 — "
                f"표시 폭 {MAX_NARRATION_WIDTH} 이하(한글 {MAX_NARRATION_WIDTH // 2}자), "
                f"현재 {display_width(sentence)}. 문장을 나누면 컷이 늘어 화면 전환이 빨라진다"
            )


def _parse_color(spec: Mapping[str, object], key: str, default: str) -> str:
    value = spec.get(key, default)
    if not isinstance(value, str) or not _valid_hex(value):
        raise VideoSpecError(f"'{key}'는 '#RRGGBB' 형식이어야 함: {value!r}")
    return value.lower()


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
        slides=_parse_slides(media_spec),
        voice=_parse_voice(media_spec),
        background=_parse_color(media_spec, "background", DEFAULT_BACKGROUND),
        background2=_parse_color(media_spec, "background2", DEFAULT_BACKGROUND2),
        foreground=_parse_color(media_spec, "foreground", DEFAULT_FOREGROUND),
        accent=_parse_color(media_spec, "accent", DEFAULT_ACCENT),
    )
