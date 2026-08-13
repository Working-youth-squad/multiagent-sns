"""카드 결정론 렌더 (FR-M1): `CardSpec` → PNG 바이트.

같은 spec → 같은 바이트(→ 같은 checksum). 좌표·폰트 크기·줄바꿈은 전부 spec에서
결정론적으로 계산하고, PNG는 메타데이터(타임스탬프 등) 없이 저장한다. 텍스트가
안전영역을 넘치면 `overflow=True`로 표시해 품질 게이트(FR-Q1)가 발행을 막게 한다.

폰트: Pillow 내장 기본 폰트(버전에 동봉되어 결정론적)를 기본 사용. 한글 전용 폰트
경로 주입은 후속(config) — 계약은 `font_path`로 열어 둔다.
"""

import io
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from sns.render.card.spec import CardSpec

# 안전영역 여백 = 가로의 8.3% (1080 → 90px). 규격은 상수로 외부화(FR-Q4 정합).
_MARGIN_RATIO = 0.083
# 블록 폰트 크기 = 가로 ÷ 계수 (작을수록 큰 글자).
_HOOK_DIV = 14
_TITLE_DIV = 18
_BODY_DIV = 24
_FOOTER_DIV = 30
# 블록 사이 세로 간격 = 가로 × 비율.
_GAP_RATIO = 0.03
# 줄 간격 = 글자 크기 × 배수.
_LINE_SPACING = 1.25


@dataclass(frozen=True)
class CardRender:
    png: bytes
    width: int
    height: int
    # 텍스트가 안전영역 세로를 초과 → 품질 게이트가 차단(FR-Q1 오버플로우).
    overflow: bool
    # (left, top, right, bottom) — FR-A2 안전영역 검사 참조점.
    safe_area: tuple[int, int, int, int]


# truetype는 FreeTypeFont, load_default(size=)는 둘 중 하나를 낸다 — 둘 다 그리기·측정 가능.
_Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


@lru_cache(maxsize=64)
def _font(size: int, font_path: str | None) -> _Font:
    if font_path is not None:
        return ImageFont.truetype(font_path, size)
    return ImageFont.load_default(size=size)


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: _Font, max_width: int) -> list[str]:
    """공백 기준 줄바꿈, 한 토큰이 폭을 넘으면 글자 단위로 쪼갠다(한글 대응)."""

    def width_of(s: str) -> float:
        left, _, right, _ = draw.textbbox((0, 0), s, font=font)
        return right - left

    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for token in paragraph.split(" "):
            if not token:
                continue
            trial = token if not current else f"{current} {token}"
            if width_of(trial) <= max_width:
                current = trial
                continue
            # 현재 줄에 이어 붙일 수 없다 → 줄을 넘긴다.
            if current:
                lines.append(current)
                current = ""
            if width_of(token) <= max_width:
                current = token
            else:  # 토큰 하나가 폭을 넘음 → 글자 단위 강제 분할.
                buf = ""
                for ch in token:
                    if buf and width_of(buf + ch) > max_width:
                        lines.append(buf)
                        buf = ch
                    else:
                        buf += ch
                current = buf
        lines.append(current)
    return lines


def render_card(spec: CardSpec, *, font_path: str | None = None) -> CardRender:
    """`CardSpec`을 결정론 PNG로 렌더. 같은 입력 → 같은 바이트."""
    bg = _hex_to_rgb(spec.palette.background)
    fg = _hex_to_rgb(spec.palette.foreground)
    accent = _hex_to_rgb(spec.palette.accent)

    img = Image.new("RGB", (spec.width, spec.height), bg)
    draw = ImageDraw.Draw(img)

    margin = round(spec.width * _MARGIN_RATIO)
    left = margin
    right = spec.width - margin
    top = margin
    bottom = spec.height - margin
    max_width = right - left
    gap = round(spec.width * _GAP_RATIO)

    # (텍스트, 폰트크기 계수, 색) 순서로 위→아래 스택.
    blocks = [
        (spec.hook, _HOOK_DIV, accent),
        (spec.title, _TITLE_DIV, fg),
        ("\n".join(spec.body), _BODY_DIV, fg),
    ]

    y = top
    for text, div, color in blocks:
        font = _font(spec.width // div, font_path)
        line_h = round((spec.width // div) * _LINE_SPACING)
        for line in _wrap(draw, text, font, max_width):
            draw.text((left, y), line, font=font, fill=color)
            y += line_h
        y += gap

    # 푸터(CTA)는 하단에 고정 배치.
    footer_font = _font(spec.width // _FOOTER_DIV, font_path)
    footer_line_h = round((spec.width // _FOOTER_DIV) * _LINE_SPACING)
    footer_lines = _wrap(draw, spec.footer, footer_font, max_width)
    footer_top = bottom - footer_line_h * len(footer_lines)
    fy = footer_top
    for line in footer_lines:
        draw.text((left, fy), line, font=footer_font, fill=accent)
        fy += footer_line_h

    # 본문 스택이 푸터 시작선을 침범 → 오버플로우(안전영역 초과).
    overflow = y > footer_top

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=6)
    return CardRender(
        png=buf.getvalue(),
        width=spec.width,
        height=spec.height,
        overflow=overflow,
        safe_area=(left, top, right, bottom),
    )
