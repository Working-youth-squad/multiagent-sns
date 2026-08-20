"""FR-Q7 이미지 게이트 — 금지 소재 차단 + 결정론 선택. 네트워크 0.

FR-Q7은 금지어/카테고리를 **코드 상수로 외부화**하고 같은 입력이 같은 판정을 내도록
요구한다(프롬프트 판정 아님). 이미지 트랙에서 이 게이트가 막아야 하는 것:
NSFW · 정치 소재 · 식별 가능한 인물 · 타사 로고(저작권).

인물을 막는 건 과하지 않다 — Pexels 라이선스 자체가 "식별 가능한 인물을 불리하게
비치도록 쓰지 말 것"을 걸고 있고, 개발 채널 배경에 모르는 사람 얼굴이 깔릴 이유도 없다.
"""

import pytest

from sns.render.images.gate import (
    BLOCKED_TERMS,
    MIN_ACCEPTABLE_SIDE,
    StockImage,
    pick_image,
    screen_image,
    screen_query,
)


def image(**over: object) -> StockImage:
    base: dict[str, object] = {
        "source": "pexels",
        "source_id": "1181671",
        "page_url": "https://www.pexels.com/photo/1181671/",
        "download_url": "https://images.pexels.com/photos/1181671/x.jpeg",
        "width": 1920,
        "height": 1280,
        "alt": "close up of source code on a dark monitor",
        "photographer": "Christina Morillo",
        "license_id": "pexels",
    }
    return StockImage(**{**base, **over})  # type: ignore[arg-type]


# ── 질의 검열 ─────────────────────────────────────────────────────


def test_clean_query_allowed() -> None:
    assert screen_query("abstract network cables").allowed


def test_query_with_blocked_term_rejected() -> None:
    verdict = screen_query("politician giving a speech")
    assert not verdict.allowed
    assert "politician" in verdict.reason


def test_query_screening_is_case_insensitive() -> None:
    assert not screen_query("Nude Portrait").allowed


def test_blocked_term_must_match_whole_word() -> None:
    """'man'이 'management'를 막으면 개발 주제 대부분이 통과 못 한다."""
    assert screen_query("project management dashboard").allowed
    assert not screen_query("man at a desk").allowed


def test_empty_query_rejected() -> None:
    with pytest.raises(ValueError, match="비지 않은"):
        screen_query("   ")


def test_non_ascii_query_rejected() -> None:
    """검색·alt가 영어라 금지어 매칭도 영어 기준이다 — 한글 질의는 게이트를 우회한다."""
    verdict = screen_query("정치인 연설")
    assert not verdict.allowed
    assert "영문" in verdict.reason


def test_every_blocked_term_actually_blocks() -> None:
    """상수 목록과 판정 로직이 어긋나지 않는지 — 목록만 늘고 로직이 안 보는 사고 방지."""
    for category, terms in BLOCKED_TERMS.items():
        for term in terms:
            verdict = screen_query(f"a photo of {term} in a studio")
            assert not verdict.allowed, f"{category}/{term}이 통과함"


# ── 후보 검열 ─────────────────────────────────────────────────────


def test_clean_candidate_allowed() -> None:
    assert screen_image(image()).allowed


def test_candidate_alt_text_screened() -> None:
    verdict = screen_image(image(alt="a rifle on a wooden table"))
    assert not verdict.allowed
    assert "rifle" in verdict.reason


def test_candidate_below_min_side_rejected() -> None:
    verdict = screen_image(image(width=MIN_ACCEPTABLE_SIDE - 1, height=2000))
    assert not verdict.allowed
    assert "해상도" in verdict.reason


def test_unknown_license_rejected() -> None:
    """소스가 늘어날 때 라이선스 확인 없이 흘러드는 걸 막는다."""
    verdict = screen_image(image(license_id="unknown"))
    assert not verdict.allowed
    assert "라이선스" in verdict.reason


def test_missing_alt_is_allowed_but_not_fatal() -> None:
    """alt는 Pexels에서 비어 오는 경우가 있다 — 없다고 떨어뜨리면 공급이 말라붙는다."""
    assert screen_image(image(alt="")).allowed


# ── 선택 ──────────────────────────────────────────────────────────


def test_pick_returns_none_when_all_blocked() -> None:
    assert pick_image([image(alt="nude model")]) is None


def test_pick_skips_blocked_and_takes_next() -> None:
    picked = pick_image([image(source_id="a", alt="nude model"), image(source_id="b")])
    assert picked is not None and picked.source_id == "b"


def test_pick_prefers_squarer_source() -> None:
    """센터 크롭이라 극단적 가로세로비는 피사체가 통째로 잘려나간다."""
    wide = image(source_id="wide", width=4000, height=1000)
    squarish = image(source_id="sq", width=1600, height=1500)
    picked = pick_image([wide, squarish])
    assert picked is not None and picked.source_id == "sq"


def test_pick_is_deterministic_regardless_of_input_order() -> None:
    a, b = (
        image(source_id="a", width=1600, height=1500),
        image(source_id="b", width=1600, height=1500),
    )
    first = pick_image([a, b])
    second = pick_image([b, a])
    assert first is not None and second is not None
    assert first.source_id == second.source_id


def test_pick_on_empty_candidates() -> None:
    assert pick_image([]) is None
