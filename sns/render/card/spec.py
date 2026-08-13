"""카드 렌더 입력 스펙 — `media_spec`(jsonb) → 검증된 `CardSpec` (FR-G3·M1).

Content Agent가 낳는 `media_spec`을 결정론 렌더의 확정 입력으로 파싱한다. 여기서
형식을 강제하므로 렌더러는 항상 온전한 값만 본다 — 누락·타입 오류는 발행 파이프라인
진입 전에 `CardSpecError`로 끊는다.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

# IG 피드 카드 기본 규격 4:5 (1080×1350). spec이 명시하면 덮어쓴다.
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1350
# 변당 최대 치수(px). `media_spec`은 Content Agent(LLM) 산출물이라 환각·오염이
# 가능한데, 상한이 없으면 거대한 값 하나가 `Image.new`에서 메모리 폭탄이 되어 워커를
# 죽인다(예: 10^8×10^8). 발행 진입 전 방어선인 파싱에서 끊는다. IG/스토리 최대
# (1080×1920)의 넉넉한 상한.
MAX_CARD_SIDE = 4096

DEFAULT_PALETTE = {
    "background": "#0d1117",
    "foreground": "#e6edf3",
    "accent": "#58a6ff",
}


class CardSpecError(ValueError):
    """malformed `media_spec` — 렌더 진입 전 차단."""


@dataclass(frozen=True)
class Palette:
    background: str  # "#RRGGBB"
    foreground: str
    accent: str


@dataclass(frozen=True)
class CardSpec:
    width: int
    height: int
    hook: str  # 첫 화면 훅 (FR-G5) — 스크롤 정지 요인
    title: str
    body: tuple[str, ...]  # 본문 단락
    footer: str  # CTA/마무리 (05 §3 체크리스트)
    palette: Palette


def _require_str(spec: Mapping[str, object], key: str, *, allow_empty: bool = False) -> str:
    value = spec.get(key)
    if not isinstance(value, str):
        raise CardSpecError(f"'{key}'는 문자열이어야 함: {value!r}")
    if not allow_empty and not value.strip():
        raise CardSpecError(f"'{key}'는 비어 있을 수 없음")
    return value


def _require_body(spec: Mapping[str, object]) -> tuple[str, ...]:
    value = spec.get("body")
    if isinstance(value, str):  # 단일 문단 허용 → 1줄 튜플로 정규화
        value = [value]
    if not isinstance(value, list) or not value:
        raise CardSpecError(f"'body'는 비지 않은 리스트여야 함: {value!r}")
    lines: list[str] = []
    for i, line in enumerate(value):
        if not isinstance(line, str):
            raise CardSpecError(f"'body[{i}]'는 문자열이어야 함: {line!r}")
        lines.append(line)
    return tuple(lines)


def _valid_hex(color: str) -> bool:
    if not (len(color) == 7 and color[0] == "#"):
        return False
    try:
        int(color[1:], 16)
    except ValueError:
        return False
    return True


def _parse_palette(spec: Mapping[str, object]) -> Palette:
    raw = spec.get("palette", DEFAULT_PALETTE)
    if not isinstance(raw, Mapping):
        raise CardSpecError(f"'palette'는 매핑이어야 함: {raw!r}")
    merged = {**DEFAULT_PALETTE, **cast(Mapping[str, object], raw)}
    colors: dict[str, str] = {}
    for key in ("background", "foreground", "accent"):
        color = merged[key]
        if not isinstance(color, str) or not _valid_hex(color):
            raise CardSpecError(f"'palette.{key}'는 '#RRGGBB' 형식이어야 함: {color!r}")
        colors[key] = color.lower()
    return Palette(**colors)


def _parse_dimension(spec: Mapping[str, object], key: str, default: int) -> int:
    value = spec.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CardSpecError(f"'{key}'는 양의 정수여야 함: {value!r}")
    if value > MAX_CARD_SIDE:
        raise CardSpecError(f"'{key}'는 {MAX_CARD_SIDE}px 이하여야 함(메모리 폭탄 방어): {value!r}")
    return value


def parse_card_spec(media_spec: Mapping[str, object]) -> CardSpec:
    """`media_spec` → `CardSpec`. 누락·형식 오류는 `CardSpecError`."""
    return CardSpec(
        width=_parse_dimension(media_spec, "width", DEFAULT_WIDTH),
        height=_parse_dimension(media_spec, "height", DEFAULT_HEIGHT),
        hook=_require_str(media_spec, "hook"),
        title=_require_str(media_spec, "title"),
        body=_require_body(media_spec),
        footer=_require_str(media_spec, "footer"),
        palette=_parse_palette(media_spec),
    )
