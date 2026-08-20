"""개념 그림 → 정사각 PNG (FR-M1 결정론).

3단 레이아웃의 가운데 칸을 채우는 세 번째 소스다. 코드([sns.render.code_image])가
1순위, 이게 2순위, 실사 스톡([sns.render.images])이 마지막이다.

**실사 스톡은 추상 개념을 못 그린다.** "list vs set"에 전선 사진이 왔다 — 검색어의
단어에만 반응할 뿐 개념과는 무관했다. 우리가 그리면 개념 자체를 그릴 수 있고,
저작권·비용·네트워크·결정론 리스크가 전부 0이다. 코드 이미지를 직접 그리기로 한 것과
같은 판단이다.

종류가 셋이고 맡는 컷이 다르다:

    emphasis  숫자·키워드 하나를 크게        "십만 곱하기 십만" 같은 충격 지점
    compare   느린 방법 vs 빠른 방법 도해     개념 전환부 — 왜 빨라지는지
    remember  마무리 한 줄 + 코드 알약        "잊지 마세요"

종류를 **닫힌 집합**으로 둔 게 핵심이다. "아무 도해나 그려주는" 스펙은 LLM이 말이 안 되는
그림을 요청하게 만든다. 우리가 잘 그릴 수 있는 것만 받고, 나머지는 파싱에서 끊는다.
"""

import io
from collections.abc import Mapping
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from sns.render.code_image import BACKGROUND, DEFAULT_SIZE, EDGE
from sns.render.fonts import FONT_CANDIDATES, MONO_CANDIDATES, pick_font
from sns.render.text import display_width, wrap_balanced

# 종류별로 받는 필드. 목록에 없는 필드는 거부한다 — 조용히 무시하면 LLM이 없는 필드를
# 계속 만들어내고, 그게 화면에 안 나오는 이유를 아무도 모르게 된다.
CONCEPT_FIELDS: dict[str, tuple[str, ...]] = {
    "emphasis": ("tag", "headline", "sub"),
    "compare": ("before_label", "before_note", "after_label", "after_note", "footer"),
    "remember": ("line", "code"),
}
# 없으면 그림이 성립하지 않는 필드.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "emphasis": ("headline",),
    "compare": ("before_label", "after_label"),
    "remember": ("line",),
}
# 표시 폭 상한(한글 1자 = 2). 전부 940 정사각 기준 실측이다.
MAX_FIELD_WIDTH: dict[str, int] = {
    "tag": 20,
    "headline": 16,  # 가장 큰 글자 — 짧아야 강조가 된다
    "sub": 28,
    "before_label": 16,
    "before_note": 16,
    "after_label": 16,
    "after_note": 16,
    "footer": 24,
    "line": 34,
    "code": 22,
}

INK = (230, 237, 243)
DIM = (139, 148, 158)
ACCENT = (88, 166, 255)
SLOW = (255, 123, 114)  # 느린 쪽
FAST = (63, 185, 80)  # 빠른 쪽
CELL_FILL = (22, 27, 34)

_CELLS = 6  # 도해의 칸 수. 홀짝 없이 가운데 정렬되고 96px 칸이 폭에 들어간다.
_EDGE_WIDTH = 3


class ConceptError(ValueError):
    """개념 그림 스펙이 잘못됨 — 렌더 진입 전 차단."""


@dataclass(frozen=True)
class Concept:
    kind: str
    fields: Mapping[str, str]


def parse_concept(raw: Mapping[str, object]) -> Concept:
    """`concept` 매핑 → 검증된 `Concept`. 미지의 종류·필드·과폭은 전부 거부."""
    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in CONCEPT_FIELDS:
        raise ConceptError(f"'kind'는 {sorted(CONCEPT_FIELDS)} 중 하나여야 함: {kind!r}")
    allowed = CONCEPT_FIELDS[kind]
    for key in raw:
        if key != "kind" and key not in allowed:
            raise ConceptError(f"'{kind}'가 모르는 필드: {key!r} (허용: {list(allowed)})")

    fields: dict[str, str] = {}
    for name in allowed:
        value = raw.get(name, "")
        if not isinstance(value, str):
            raise ConceptError(f"'{name}'은 문자열이어야 함: {value!r}")
        text = value.strip()
        if display_width(text) > MAX_FIELD_WIDTH[name]:
            raise ConceptError(
                f"'{name}'이 정사각 폭을 넘음 — 표시 폭 {MAX_FIELD_WIDTH[name]} 이하"
                f"(한글 {MAX_FIELD_WIDTH[name] // 2}자): 현재 {display_width(text)}"
            )
        fields[name] = text
    for name in REQUIRED_FIELDS[kind]:
        if not fields[name]:
            raise ConceptError(f"'{kind}'에는 '{name}'이 필요함")
    return Concept(kind=kind, fields=fields)


# ── 그리기 도구 ───────────────────────────────────────────────────


def _text_size(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont
) -> tuple[int, int]:
    x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
    return round(x1 - x0), round(y1 - y0)


def _centered(draw: ImageDraw.ImageDraw, size: int, y: int, text: str,
              font: ImageFont.FreeTypeFont, fill: tuple[int, int, int]) -> int:  # fmt: skip
    """가로 중앙에 그리고 **다음 y**를 돌려준다. bbox 기준이라 글꼴 어센더에 안 밀린다."""
    x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
    draw.text(((size - round(x1 - x0)) // 2 - x0, y - y0), text, font=font, fill=fill)
    return y + round(y1 - y0)


def _font_for(text: str, mono_path: str, kor_path: str, size: int) -> ImageFont.FreeTypeFont:
    """고정폭이 어울리는 자리라도 **한글이 섞이면 본문 폰트**로 간다.

    고정폭 후보(Cascadia·Consolas·DejaVu)에는 한글 글리프가 없어 그대로 그리면
    두부(□)가 박힌다. 실제로 에이전트가 라벨에 "리스트"·"세트"를 써서 그렇게 나왔다.
    CJK 폰트는 라틴 글리프도 있으므로 섞인 문자열도 이쪽이면 전부 그려진다.
    """
    return ImageFont.truetype(mono_path if text.isascii() else kor_path, size)


def _fit_font(
    draw: ImageDraw.ImageDraw, text: str, path: str, start: int, max_w: int
) -> ImageFont.FreeTypeFont:
    """폭에 들어갈 때까지 2씩 줄인다 — 결정론 탐색(코드 이미지와 같은 규율)."""
    size = start
    font = ImageFont.truetype(path, size)
    while size > 28 and _text_size(draw, text, font)[0] > max_w:
        size -= 2
        font = ImageFont.truetype(path, size)
    return font


def _pill(draw: ImageDraw.ImageDraw, size: int, y: int, text: str, font: ImageFont.FreeTypeFont,
          fill: tuple[int, int, int], ink: tuple[int, int, int], *,
          outline: tuple[int, int, int] | None = None) -> int:  # fmt: skip
    """알약 — 글자 bbox 기준으로 상하좌우 여백을 같게 잡는다."""
    x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
    pad_x, pad_y = 34, 18
    w, h = round(x1 - x0) + pad_x * 2, round(y1 - y0) + pad_y * 2
    x = (size - w) // 2
    box = (x, y, x + w, y + h)
    if outline is not None:
        draw.rounded_rectangle(box, radius=16, fill=fill, outline=outline, width=3)
    else:
        draw.rounded_rectangle(box, radius=h // 2, fill=fill)
    draw.text((x + pad_x - x0, y + pad_y - y0), text, font=font, fill=ink)
    return y + h


def _down_arrow(draw: ImageDraw.ImageDraw, cx: int, top: int, height: int,
                color: tuple[int, int, int], width: int) -> None:  # fmt: skip
    """아래를 가리키는 화살표. 스파이크의 짧은 선분은 '훑는다'로 안 읽혔다."""
    head = max(height // 2, 10)
    draw.line((cx, top, cx, top + height - head), fill=color, width=width)
    draw.polygon(
        [(cx - head // 2, top + height - head), (cx + head // 2, top + height - head),
         (cx, top + height)],
        fill=color,
    )  # fmt: skip


def _cells_row(draw: ImageDraw.ImageDraw, size: int, y: int, cell: int, gap: int,
               target: int, color: tuple[int, int, int]) -> None:  # fmt: skip
    x = (size - (_CELLS * cell + (_CELLS - 1) * gap)) // 2
    for i in range(_CELLS):
        hit = i == target
        draw.rounded_rectangle(
            (x, y, x + cell, y + cell), radius=cell // 7,
            fill=color if hit else CELL_FILL, outline=color if hit else EDGE, width=3,
        )  # fmt: skip
        x += cell + gap


def _cell_center(size: int, index: int, cell: int, gap: int) -> int:
    return (size - (_CELLS * cell + (_CELLS - 1) * gap)) // 2 + index * (cell + gap) + cell // 2


# ── 종류별 레이아웃 ───────────────────────────────────────────────


def _draw_emphasis(draw: ImageDraw.ImageDraw, size: int, f: Mapping[str, str],
                   kor: str, mono: str) -> None:  # fmt: skip
    """숫자 하나를 크게. 블록 전체를 세로 중앙에 둔다 — 고정 y는 아래를 휑하게 남긴다."""
    inner = size - round(size * 0.15)
    tag_font = ImageFont.truetype(kor, round(size / 23.5))
    sub_font = ImageFont.truetype(kor, round(size / 20.4))
    # 숫자는 고정폭이 더 단단해 보인다. 한글이 섞이면 본문 폰트로([_font_for]).
    head_path = mono if f["headline"].isascii() else kor
    head_font = _fit_font(draw, f["headline"], head_path, round(size / 6.3), inner)

    tag_h = _text_size(draw, f["tag"], tag_font)[1] if f["tag"] else 0
    head_h = _text_size(draw, f["headline"], head_font)[1]
    sub_lines = (
        wrap_balanced(f["sub"], lambda s: _text_size(draw, s, sub_font)[0], inner)
        if f["sub"]
        else []
    )
    sub_step = round(sub_font.size * 1.35)
    rule_gap = round(size * 0.055)

    block = tag_h + (round(size * 0.07) if f["tag"] else 0) + head_h
    if sub_lines:
        block += rule_gap * 2 + sub_step * len(sub_lines)
    y = (size - block) // 2

    if f["tag"]:
        y = _centered(draw, size, y, f["tag"], tag_font, DIM) + round(size * 0.07)
    y = _centered(draw, size, y, f["headline"], head_font, ACCENT)
    if sub_lines:
        y += rule_gap
        draw.rectangle(((size - 128) // 2, y, (size + 128) // 2, y + 5), fill=EDGE)
        y += rule_gap
        for line in sub_lines:
            _centered(draw, size, y, line, sub_font, INK)
            y += sub_step


def _draw_compare(draw: ImageDraw.ImageDraw, size: int, f: Mapping[str, str],
                  kor: str, mono: str) -> None:  # fmt: skip
    """위=느린 방법(칸마다 훑음), 아래=빠른 방법(곧바로 꽂힘)."""
    label_size, foot_size = round(size / 21.4), round(size / 16.8)
    note_font = ImageFont.truetype(kor, round(size / 26.1))
    foot_font = _font_for(f["footer"], mono, kor, foot_size)
    cell, gap = round(size * 0.102), round(size * 0.019)
    target = _CELLS - 1

    def row(y: int, label: str, note: str, color: tuple[int, int, int], *, scan: bool) -> int:
        label_font = _font_for(label, mono, kor, label_size)
        y = _centered(draw, size, y, label, label_font, color) + round(size * 0.028)
        arrow_h = round(size * 0.052)
        if scan:
            # 칸마다 하나씩 — "처음부터 끝까지 훑는다"가 이 반복으로 읽힌다.
            for i in range(_CELLS):
                _down_arrow(draw, _cell_center(size, i, cell, gap), y, arrow_h, color, 4)
        else:
            _down_arrow(draw, _cell_center(size, target, cell, gap), y, arrow_h, color, 9)
        y += arrow_h + round(size * 0.017)
        _cells_row(draw, size, y, cell, gap, target, color)
        y += cell + round(size * 0.032)
        if note:
            y = _centered(draw, size, y, note, note_font, DIM)
        return y

    y = row(round(size * 0.075), f["before_label"], f["before_note"], SLOW, scan=True)
    y += round(size * 0.045)
    draw.line((round(size * 0.12), y, size - round(size * 0.12), y), fill=EDGE, width=3)
    y = row(y + round(size * 0.045), f["after_label"], f["after_note"], FAST, scan=False)
    if f["footer"]:
        _centered(draw, size, y + round(size * 0.05), f["footer"], foot_font, ACCENT)


def _draw_remember(draw: ImageDraw.ImageDraw, size: int, f: Mapping[str, str],
                   kor: str, mono: str) -> None:  # fmt: skip
    """마무리 — 배지 + 한 줄 + 코드 알약. 블록 전체를 세로 중앙에."""
    badge_font = ImageFont.truetype(kor, round(size / 24.7))
    body_font = ImageFont.truetype(kor, round(size / 15.2))
    code_font = _font_for(f["code"], mono, kor, round(size / 18.1))
    inner = size - round(size * 0.16)

    lines = wrap_balanced(f["line"], lambda s: _text_size(draw, s, body_font)[0], inner)
    body_step = round(body_font.size * 1.35)
    badge_h = _text_size(draw, "기억하세요", badge_font)[1] + 36
    code_h = (_text_size(draw, f["code"], code_font)[1] + 40) if f["code"] else 0

    block = badge_h + round(size * 0.075) + body_step * len(lines)
    if code_h:
        block += round(size * 0.055) + code_h
    y = (size - block) // 2

    y = _pill(draw, size, y, "기억하세요", badge_font, SLOW, (16, 18, 22)) + round(size * 0.075)
    for line in lines:
        _centered(draw, size, y, line, body_font, INK)
        y += body_step
    if f["code"]:
        _pill(draw, size, y + round(size * 0.055), f["code"], code_font, CELL_FILL, ACCENT,
              outline=ACCENT)  # fmt: skip


_LAYOUTS = {"emphasis": _draw_emphasis, "compare": _draw_compare, "remember": _draw_remember}


def render_concept_square(
    concept: Concept,
    *,
    size: int = DEFAULT_SIZE,
    font_path: str | None = None,
    mono_path: str | None = None,
) -> bytes:
    """`Concept` → 정사각 PNG 바이트. 같은 입력 → 같은 바이트."""
    kor, _ = pick_font(font_path, FONT_CANDIDATES)
    mono, _ = pick_font(mono_path, MONO_CANDIDATES)

    img = Image.new("RGB", (size, size), BACKGROUND)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, size - 1, size - 1), outline=EDGE, width=_EDGE_WIDTH)
    _LAYOUTS[concept.kind](draw, size, concept.fields, kor, mono)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()
