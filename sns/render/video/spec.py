"""영상 렌더 입력 스펙 — `media_spec`(jsonb) → 검증된 `VideoSpec` (FR-G2·M2).

카드 spec(`sns.render.card.spec`)과 같은 방어선: Content Agent(LLM) 산출물을
결정론 렌더의 확정 입력으로 파싱하고, 누락·타입 오류·환각 치수는 렌더 진입 전에
`VideoSpecError`로 끊는다. 슬라이드 1장의 텍스트 = 화면 텍스트 = 자막 = TTS 발화.
"""

from collections.abc import Mapping
from dataclasses import dataclass

# 쇼츠/릴스 세로 규격 9:16 (FR-M2). spec이 명시하면 덮어쓰되 비율은 강제.
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920
# 변당 최대 치수 — 카드 spec의 MAX_CARD_SIDE와 같은 메모리 폭탄 방어.
MAX_SIDE = 4096
# 슬라이드 수 상한: 쇼츠 최대 180s ÷ 화면 전환 최소 2~4s(FR-A2) 감안한 넉넉한 값.
MAX_SLIDES = 60

DEFAULT_VOICE = "ko-KR-Chirp3-HD-Charon"
DEFAULT_BACKGROUND = "#0d1117"
DEFAULT_FOREGROUND = "#e6edf3"


class VideoSpecError(ValueError):
    """malformed `media_spec` — 렌더 진입 전 차단."""


@dataclass(frozen=True)
class VideoSpec:
    width: int
    height: int
    slides: tuple[str, ...]  # 장당 화면 텍스트 = 자막 = TTS 발화
    voice: str  # TTS 보이스 이름 (예: ko-KR-Chirp3-HD-Charon)
    background: str  # "#RRGGBB"
    foreground: str


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


def _parse_slides(spec: Mapping[str, object]) -> tuple[str, ...]:
    value = spec.get("slides")
    if not isinstance(value, list) or not value:
        raise VideoSpecError(f"'slides'는 비지 않은 리스트여야 함: {value!r}")
    if len(value) > MAX_SLIDES:
        raise VideoSpecError(f"'slides'는 {MAX_SLIDES}장 이하여야 함: {len(value)}장")
    slides: list[str] = []
    for i, text in enumerate(value):
        if not isinstance(text, str) or not text.strip():
            raise VideoSpecError(f"'slides[{i}]'는 비지 않은 문자열이어야 함: {text!r}")
        slides.append(text)
    return tuple(slides)


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
        foreground=_parse_color(media_spec, "foreground", DEFAULT_FOREGROUND),
    )
