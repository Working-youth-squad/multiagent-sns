"""개념 그림 — 우리가 그리는 정사각. 결정론(FR-M1), 네트워크 0, 저작권 0.

실사 스톡은 추상 개념을 못 그린다("list vs set"에 전선 사진이 왔다). 코드 이미지와 같은
논리다 — 개발 콘텐츠에서 가장 주제에 맞는 그림은 **우리가 그린 것**이다.

종류가 셋이고 각각 맡는 컷이 다르다:
  emphasis  숫자·키워드 하나를 크게      "십만 곱하기 십만"
  compare   느린 방법 vs 빠른 방법 도해   개념 전환부
  remember  마무리 한 줄                 "잊지 마세요"
"""

import io

import pytest
from PIL import Image

from sns.render.code_image import BACKGROUND
from sns.render.concept_image import (
    CONCEPT_FIELDS,
    MAX_FIELD_WIDTH,
    ConceptError,
    _font_for,
    parse_concept,
    render_concept_square,
)
from sns.render.fonts import FontNotFoundError

EMPHASIS: dict[str, object] = {
    "kind": "emphasis",
    "tag": "최악의 경우",
    "headline": "100억",
    "sub": "십만 건 × 십만 건 비교",
}
COMPARE: dict[str, object] = {
    "kind": "compare",
    "before_label": "list",
    "before_note": "6번 비교",
    "after_label": "set",
    "after_note": "1번 비교",
    "footer": "O(n) → O(1)",
}
REMEMBER: dict[str, object] = {
    "kind": "remember",
    "line": "반복문 안에서 in을 쓴다면",
    "code": "set(...)",
}
ALL = (EMPHASIS, COMPARE, REMEMBER)


def opened(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


# ── 파싱 ──────────────────────────────────────────────────────────


def test_parse_keeps_given_fields() -> None:
    concept = parse_concept(EMPHASIS)
    assert concept.kind == "emphasis"
    assert concept.fields["headline"] == "100억"


def test_optional_fields_default_to_empty() -> None:
    concept = parse_concept({"kind": "emphasis", "headline": "100억"})
    assert concept.fields["tag"] == "" and concept.fields["sub"] == ""


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ConceptError, match="kind"):
        parse_concept({"kind": "pie_chart", "headline": "x"})


def test_missing_kind_rejected() -> None:
    with pytest.raises(ConceptError, match="kind"):
        parse_concept({"headline": "100억"})


def test_unknown_field_rejected() -> None:
    """조용히 무시하면 LLM이 없는 필드를 계속 만들어낸다 — 즉시 되돌려준다."""
    with pytest.raises(ConceptError, match="subtitle"):
        parse_concept({**EMPHASIS, "subtitle": "몰래 낀 필드"})


def test_missing_required_field_rejected() -> None:
    with pytest.raises(ConceptError, match="headline"):
        parse_concept({"kind": "emphasis", "tag": "최악의 경우"})


def test_field_over_width_rejected() -> None:
    with pytest.raises(ConceptError, match="headline"):
        parse_concept({**EMPHASIS, "headline": "가" * (MAX_FIELD_WIDTH["headline"] // 2 + 1)})


def test_non_string_field_rejected() -> None:
    with pytest.raises(ConceptError, match="headline"):
        parse_concept({**EMPHASIS, "headline": 100})


# ── 렌더 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", ALL, ids=lambda r: str(r["kind"]))
def test_renders_square_png(raw: dict[str, object]) -> None:
    png = render_concept_square(parse_concept(raw), size=940)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert opened(png).size == (940, 940)


@pytest.mark.parametrize("raw", ALL, ids=lambda r: str(r["kind"]))
def test_deterministic(raw: dict[str, object]) -> None:
    concept = parse_concept(raw)
    assert render_concept_square(concept) == render_concept_square(concept)


@pytest.mark.parametrize("raw", ALL, ids=lambda r: str(r["kind"]))
def test_matches_code_image_background(raw: dict[str, object]) -> None:
    """코드 이미지와 같은 슬롯을 채운다 — 배경이 다르면 컷마다 판이 바뀐 것처럼 보인다."""
    img = opened(render_concept_square(parse_concept(raw)))
    assert img.getpixel((470, 12)) == BACKGROUND


@pytest.mark.parametrize("raw", ALL, ids=lambda r: str(r["kind"]))
def test_nothing_bleeds_past_the_margin(raw: dict[str, object]) -> None:
    """글자가 정사각 밖으로 새면 옆 컷과 겹쳐 보인다 — 가장자리 열은 배경이어야 한다."""
    img = opened(render_concept_square(parse_concept(raw)))
    for x in (6, 933):
        column = {img.getpixel((x, y)) for y in range(60, 880, 7)}
        assert column == {BACKGROUND}, f"x={x}에 배경 아닌 픽셀: {column - {BACKGROUND}}"


def test_long_headline_shrinks_instead_of_overflowing() -> None:
    """상한 안이어도 긴 문자열은 폭을 넘길 수 있다 — 크기를 줄여 담는다."""
    long_one = parse_concept({**EMPHASIS, "headline": "가" * (MAX_FIELD_WIDTH["headline"] // 2)})
    img = opened(render_concept_square(long_one))
    for x in (6, 933):
        assert {img.getpixel((x, y)) for y in range(60, 880, 7)} == {BACKGROUND}


def test_compare_marks_both_rows_distinctly() -> None:
    """느린 쪽과 빠른 쪽이 한눈에 갈려야 한다 — 색이 같으면 도해가 아무것도 말하지 않는다."""
    img = opened(render_concept_square(parse_concept(COMPARE), size=940))
    colors = {img.getpixel((x, y)) for x in range(80, 860, 4) for y in range(150, 800, 4)}
    reddish = {c for c in colors if c[0] > 150 and c[0] > c[1] + 50}
    greenish = {c for c in colors if c[1] > 130 and c[1] > c[0] + 50}
    assert reddish, "느린 쪽 표시가 없음"
    assert greenish, "빠른 쪽 표시가 없음"


def test_every_field_is_covered_by_exactly_one_metadata_table() -> None:
    """필드가 어느 표에도 없으면 파서가 상한 없이 통과시키고, 둘에 있으면 판정이 갈린다."""
    from sns.render.concept_image import INDEX_FIELDS, LIST_FIELDS

    for kind, fields in CONCEPT_FIELDS.items():
        for field in fields:
            tables = [
                field in INDEX_FIELDS,
                field in LIST_FIELDS,  # 목록은 항목별 폭도 MAX_FIELD_WIDTH에서 본다
                field in MAX_FIELD_WIDTH and field not in LIST_FIELDS,
            ]
            assert sum(tables) == 1, f"{kind}/{field}의 메타데이터가 {sum(tables)}곳"


def test_list_fields_also_declare_an_item_width() -> None:
    from sns.render.concept_image import LIST_FIELDS

    for name in LIST_FIELDS:
        assert name in MAX_FIELD_WIDTH, f"{name}의 항목 폭 상한 없음"


def test_hangul_text_never_uses_the_mono_font() -> None:
    """고정폭 후보에는 한글 글리프가 없다 — 그대로 그리면 두부(□)가 박힌다.

    실제로 에이전트가 compare 라벨에 "리스트"·"세트"를 써서 영상에 두부가 나갔다.
    ASCII 고정 픽스처(list/set)만 두면 이 경로가 테스트에 안 걸린다.
    """
    from sns.render.fonts import FONT_CANDIDATES, MONO_CANDIDATES, pick_font

    mono, _ = pick_font(None, MONO_CANDIDATES)
    kor, _ = pick_font(None, FONT_CANDIDATES)
    assert _font_for("set", mono, kor, 40).path == mono
    assert _font_for("리스트", mono, kor, 40).path == kor
    assert _font_for("set 리스트", mono, kor, 40).path == kor, "섞인 문자열도 CJK 폰트로"


def test_korean_labels_render_without_crashing() -> None:
    """에이전트는 라벨을 한글로 쓴다 — 픽스처를 영어로만 두면 그 경로가 안 돈다."""
    korean = parse_concept(
        {
            "kind": "compare",
            "before_label": "리스트",
            "before_note": "순차 탐색",
            "after_label": "세트",
            "after_note": "해시 탐색",
            "footer": "느림 → 빠름",
        }
    )
    assert opened(render_concept_square(korean)).size == (940, 940)


def test_missing_font_raises_instead_of_tofu(monkeypatch: pytest.MonkeyPatch) -> None:
    import sns.render.concept_image as mod

    monkeypatch.setattr(mod, "FONT_CANDIDATES", ())
    with pytest.raises(FontNotFoundError):
        render_concept_square(parse_concept(EMPHASIS))


# ── 추가 템플릿 3종 (코드 없는 영상용) ────────────────────────────
#
# 도구 소개·트렌드 주제는 보여줄 코드가 없어 정사각이 통째로 비었다(실제로 6컷 전부).
# 기존 3종만으로 채우면 같은 카드가 반복돼 매번 같은 영상처럼 보인다.

FLOW: dict[str, object] = {
    "kind": "flow",
    "steps": ["주제 한 줄 입력", "AI가 대본 작성", "영상·음성 합성"],
    "active": 1,
}
STEPS: dict[str, object] = {
    "kind": "steps",
    "items": ["대본 자동 생성", "영상 자동 합성", "자막·TTS 자동"],
    "active": 2,
}
TERMINAL: dict[str, object] = {
    "kind": "terminal",
    "commands": ["git clone MoneyPrinter", "pip install -r req.txt"],
    "note": "깃허브에서 무료",
}
NEW_KINDS = (FLOW, STEPS, TERMINAL)


@pytest.mark.parametrize("raw", NEW_KINDS, ids=lambda r: str(r["kind"]))
def test_new_kinds_render_and_are_deterministic(raw: dict[str, object]) -> None:
    concept = parse_concept(raw)
    png = render_concept_square(concept)
    assert opened(png).size == (940, 940)
    assert png == render_concept_square(concept)


@pytest.mark.parametrize("raw", NEW_KINDS, ids=lambda r: str(r["kind"]))
def test_new_kinds_stay_inside_the_square(raw: dict[str, object]) -> None:
    img = opened(render_concept_square(parse_concept(raw)))
    for x in (6, 933):
        assert {img.getpixel((x, y)) for y in range(60, 880, 7)} == {BACKGROUND}


def test_list_field_is_required() -> None:
    with pytest.raises(ConceptError, match="steps"):
        parse_concept({"kind": "flow", "active": 0})


def test_list_field_rejects_non_list() -> None:
    with pytest.raises(ConceptError, match="steps"):
        parse_concept({"kind": "flow", "steps": "한 줄", "active": 0})


def test_too_many_items_rejected() -> None:
    """flow 상자가 4개면 상자가 얇아져 글자가 안 들어간다 — 실측으로 3개가 상한."""
    with pytest.raises(ConceptError, match="steps"):
        parse_concept({"kind": "flow", "steps": ["가", "나", "다", "라"], "active": 0})


def test_item_over_width_rejected() -> None:
    with pytest.raises(ConceptError, match="steps"):
        parse_concept({"kind": "flow", "steps": ["가" * 40], "active": 0})


def test_active_out_of_range_rejected() -> None:
    """강조 위치가 목록 밖이면 아무것도 강조되지 않은 채 조용히 그려진다."""
    with pytest.raises(ConceptError, match="active"):
        parse_concept({**FLOW, "active": 5})


def test_active_defaults_to_first() -> None:
    concept = parse_concept({"kind": "flow", "steps": ["가", "나"]})
    assert concept.fields["active"] == 0


def test_active_must_be_an_integer() -> None:
    with pytest.raises(ConceptError, match="active"):
        parse_concept({**FLOW, "active": "1"})


def test_flow_highlight_moves_the_picture() -> None:
    """같은 그림에 강조만 옮겨 컷을 잇는다 — 코드의 focus_lines와 같은 원리."""
    first = render_concept_square(parse_concept({**FLOW, "active": 0}))
    last = render_concept_square(parse_concept({**FLOW, "active": 2}))
    assert first != last


def test_terminal_note_is_optional() -> None:
    assert opened(render_concept_square(parse_concept({
        "kind": "terminal", "commands": ["pip install sns"]
    }))).size == (940, 940)  # fmt: skip


def test_long_command_fits_inside_the_terminal_window() -> None:
    """실 에이전트가 'pip install -r requirements.txt'를 써서 창 밖으로 넘쳤다.

    글자 수 상한만으로는 부족하다 — 같은 글자 수라도 고정폭 폰트에서 실제 폭이 다르다.
    창 폭에 맞춰 크기를 줄여야 한다(코드 이미지의 _fit_font_size와 같은 규율).
    """
    concept = parse_concept(
        {"kind": "terminal", "commands": ["pip install -r requirements.txt"], "note": "설치"}
    )
    img = opened(render_concept_square(concept, size=940))
    margin = round(940 * 0.064)
    # 창 오른쪽 테두리 바로 안쪽. 글자는 밝고(INK) 창·바탕은 어둡다 — 밝은 픽셀이 있으면
    # 명령이 테두리까지 닿았다는 뜻이다(세로 위치는 내용에 따라 달라지므로 밝기로 본다).
    brightest = max(
        max(img.getpixel((x, y)))
        for x in range(940 - margin - 14, 940 - margin - 2)
        for y in range(200, 740, 2)
    )
    assert brightest < 100, f"명령이 창 밖으로 넘침 — 가장 밝은 픽셀 {brightest}"


def test_short_command_keeps_the_full_size() -> None:
    """짧은 명령까지 줄이면 읽기만 나빠진다 — 넘칠 때만 줄인다."""
    from sns.render.concept_image import terminal_font_size

    assert terminal_font_size(["pip install sns"], 940) > terminal_font_size(
        ["pip install -r requirements.txt"], 940
    )


@pytest.mark.parametrize(
    "command",
    [
        "pip install -r requirements.txt",
        "docker compose up -d postgres --build",
        "uv run python -m sns.runner.cycle --goal g",
        "npx create-next-app@latest my-app --typescript",
    ],
)
def test_fitted_command_measures_within_the_window(command: str) -> None:
    """폰트를 **실제로 재서** 창 안에 드는지 본다 — 위 픽셀 테스트는 CI 폰트에서만 걸린다.

    자간이 폰트마다 달라(Cascadia 0.586em, DejaVu 0.602em) 계수로 역산하면 어느 한쪽에서
    반드시 넘친다. 실측이면 어느 폰트로 돌리든 이 단언이 성립한다 — 그게 요점이다.
    """
    from PIL import ImageFont

    from sns.render.concept_image import terminal_font_size, terminal_text_budget
    from sns.render.fonts import MONO_CANDIDATES, pick_font

    path, _ = pick_font(None, MONO_CANDIDATES)
    px = terminal_font_size([command], 940)
    width = ImageFont.truetype(path, px).getlength(command)
    budget = terminal_text_budget(940)
    assert width <= budget, f"{px}px에서 폭 {width:.0f} > 예산 {budget}"
