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


def test_every_kind_has_field_metadata() -> None:
    """필드 목록과 폭 상한이 어긋나면 파서가 상한 없는 필드를 통과시킨다."""
    for kind, fields in CONCEPT_FIELDS.items():
        for field in fields:
            assert field in MAX_FIELD_WIDTH, f"{kind}/{field}의 폭 상한 없음"


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
