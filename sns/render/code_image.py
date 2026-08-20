"""코드 스니펫 → 정사각 PNG (FR-M1 결정론).

3단 레이아웃(주제 / 정사각 / 자막)의 가운데 칸을 채우는 이미지 소스 중 하나다.
개발 콘텐츠에서 코드는 **가장 주제에 맞는 이미지**이면서, 제3자 이미지가 지고 오는
비용·저작권(FR-Q7)·결정론 리스크를 전부 피한다 — 우리가 직접 그리기 때문이다.

두 가지가 이 모듈의 까다로운 부분이다:

1. **한글 폰트 폴백** — 고정폭 폰트(Cascadia·Consolas)에 한글 글리프가 없어 주석이
   두부(□)로 박힌다. 한글 주석은 흔하므로 글자별로 폰트를 갈아 끼운다(`display_runs`).
2. **폰트 크기 역산** — 코드 길이가 제각각이라 고정 크기로는 넘치거나 휑하다.
   가장 긴 줄이 폭에 들어갈 때까지 크기를 낮춘다. 탐색이 결정론이라 같은 코드면 같은 크기.
"""

import io

from PIL import Image, ImageDraw, ImageFont
from pygments import lex
from pygments.lexer import Lexer
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.token import Token, _TokenType
from pygments.util import ClassNotFound

from sns.render.fonts import FONT_CANDIDATES, MONO_CANDIDATES, pick_font

DEFAULT_SIZE = 940  # 3단 레이아웃의 정사각 변 (1080 - 좌우 여백 70×2)
MAX_CODE_LINES = 18  # 이 이상은 정사각에서 읽을 수 없는 크기가 된다
MAX_FONT_SIZE = 40
MIN_FONT_SIZE = 16
_PAD_RATIO = 0.058  # 정사각 대비 안쪽 여백
_LINE_SPACING = 1.55

BACKGROUND = (13, 17, 23)
EDGE = (48, 58, 72)
PLAIN = (230, 237, 243)

# github-dark 계열. 없는 토큰은 부모를 타고 올라가 PLAIN으로 수렴한다.
TOKEN_COLORS: dict[_TokenType, tuple[int, int, int]] = {
    Token.Keyword: (255, 123, 114),
    Token.Name.Function: (210, 168, 255),
    Token.Name.Class: (210, 168, 255),
    Token.Name.Builtin: (121, 192, 255),
    Token.Name.Decorator: (210, 168, 255),
    Token.Literal.String: (165, 214, 255),
    Token.Literal.Number: (121, 192, 255),
    Token.Comment: (139, 148, 158),
    Token.Operator: (255, 123, 114),
    Token.Operator.Word: (255, 123, 114),
    Token.Punctuation: (201, 209, 217),
}


class CodeImageError(ValueError):
    """코드가 정사각에 담길 수 없음 — 렌더 진입 전 차단."""


def _is_hangul(ch: str) -> bool:
    return "가" <= ch <= "힣" or "㄰" <= ch <= "㆏"


def display_runs(text: str) -> list[tuple[str, bool]]:
    """문자열을 (조각, 한글여부) 런으로 쪼갠다.

    고정폭 폰트에 한글이 없어 한글만 다른 폰트로 그려야 한다. 인접한 같은 종류는 합쳐
    draw 호출을 줄인다.
    """
    runs: list[tuple[str, bool]] = []
    for ch in text:
        hangul = _is_hangul(ch)
        if runs and runs[-1][1] == hangul:
            runs[-1] = (runs[-1][0] + ch, hangul)
        else:
            runs.append((ch, hangul))
    return runs


def _color(token: _TokenType) -> tuple[int, int, int]:
    while token not in TOKEN_COLORS and token.parent is not None:
        token = token.parent
    return TOKEN_COLORS.get(token, PLAIN)


def _lexer(code: str, lang: str | None) -> Lexer:
    if lang:
        try:
            return get_lexer_by_name(lang)
        except ClassNotFound:
            pass  # 알 수 없는 언어명은 추측으로 넘어간다 — 하이라이트가 없을 뿐 렌더는 된다
    try:
        return guess_lexer(code)
    except ClassNotFound:
        return get_lexer_by_name("text")


def _run_width(draw: ImageDraw.ImageDraw, text: str, mono: ImageFont.FreeTypeFont,
               kor: ImageFont.FreeTypeFont) -> float:  # fmt: skip
    return sum(
        draw.textbbox((0, 0), piece, font=(kor if hangul else mono))[2]
        for piece, hangul in display_runs(text)
    )


def _draw_run(draw: ImageDraw.ImageDraw, x: float, y: float, text: str,
              mono: ImageFont.FreeTypeFont, kor: ImageFont.FreeTypeFont,
              fill: tuple[int, int, int]) -> float:  # fmt: skip
    for piece, hangul in display_runs(text):
        font = kor if hangul else mono
        draw.text((x, y), piece, font=font, fill=fill)
        x += draw.textbbox((0, 0), piece, font=font)[2]
    return x


def _fit_font_size(draw: ImageDraw.ImageDraw, lines: list[str], inner: int, mono_path: str,
                   kor_path: str) -> int:  # fmt: skip
    """가장 긴 줄과 전체 높이가 안쪽 영역에 들어가는 최대 크기. 결정론 탐색."""
    for size in range(MAX_FONT_SIZE, MIN_FONT_SIZE - 1, -2):
        mono = ImageFont.truetype(mono_path, size)
        kor = ImageFont.truetype(kor_path, size)
        widest = max((_run_width(draw, line, mono, kor) for line in lines), default=0)
        if widest <= inner and len(lines) * round(size * _LINE_SPACING) <= inner:
            return size
    return MIN_FONT_SIZE


def render_code_square(
    code: str,
    *,
    lang: str | None = None,
    size: int = DEFAULT_SIZE,
    mono_path: str | None = None,
    font_path: str | None = None,
) -> bytes:
    """코드 → 정사각 PNG 바이트. 같은 입력 → 같은 바이트."""
    lines = code.rstrip().split("\n")
    if not any(line.strip() for line in lines):
        raise CodeImageError("코드가 비어 있음")
    if len(lines) > MAX_CODE_LINES:
        raise CodeImageError(f"코드는 {MAX_CODE_LINES}줄 이하여야 함: {len(lines)}줄")

    mono_resolved, _ = pick_font(mono_path, MONO_CANDIDATES)
    kor_resolved, _ = pick_font(font_path, FONT_CANDIDATES)

    img = Image.new("RGB", (size, size), BACKGROUND)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, size - 1, size - 1), outline=EDGE, width=3)

    pad = round(size * _PAD_RATIO)
    font_size = _fit_font_size(draw, lines, size - pad * 2, mono_resolved, kor_resolved)
    mono = ImageFont.truetype(mono_resolved, font_size)
    kor = ImageFont.truetype(kor_resolved, font_size)
    line_h = round(font_size * _LINE_SPACING)

    y = (size - line_h * len(lines)) // 2
    x = float(pad)
    for token, text in lex(code.rstrip(), _lexer(code, lang)):
        parts = text.split("\n")
        for i, piece in enumerate(parts):
            if piece:
                x = _draw_run(draw, x, y, piece, mono, kor, _color(token))
            if i < len(parts) - 1:  # 토큰 하나가 개행을 여럿 품을 수 있다
                y, x = y + line_h, float(pad)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()
