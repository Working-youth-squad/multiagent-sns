"""등수 통계 — 백분위 변환·결측 페널티·관측값 분산·정렬 (04-트렌드조사 §5).

여기서 지키는 것 둘: ①소스 원값을 받는 경로가 없을 것(입력이 문자열 순서열뿐이라
시그니처로 강제된다) ②대입값이 분산에 들어가지 않을 것. 후자는 실측에서 통계를 무의미하게
만들었던 결함이라 회귀 케이스로 못 박는다.
"""

from statistics import pstdev

import pytest

from sns.research.ranking import (
    MISSING_PCT_RANK,
    KeywordStat,
    SourceRank,
    band_bounds,
    excluded_by,
    pct_rank_of,
    percentile,
    rank_stats,
)
from sns.tools.contracts import SourceResult


def ok(source: str, *items: str) -> SourceResult:
    return SourceResult(source=source, ok=True, items=items)


def fail(source: str) -> SourceResult:
    return SourceResult(source=source, ok=False)


def by_text(stats: tuple[KeywordStat, ...]) -> dict[str, KeywordStat]:
    return {s.text: s for s in stats}


# ── 백분위 변환 ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("index", "length", "expected"),
    [(0, 3, 0.25), (1, 3, 0.5), (2, 3, 0.75), (0, 1, 0.5)],
)
def test_pct_rank_divides_by_length_plus_one(index: int, length: int, expected: float) -> None:
    assert pct_rank_of(index, length) == pytest.approx(expected)


def test_observed_rank_never_reaches_missing_penalty() -> None:
    """'관측된 꼴찌'와 '아예 없었다'가 같은 값이 되면 안 된다 — 분모 +1의 존재 이유."""
    assert pct_rank_of(49, 50) < MISSING_PCT_RANK


def test_pct_rank_rejects_negative_length() -> None:
    with pytest.raises(ValueError, match="음수"):
        pct_rank_of(0, -1)


# ── 집계 ─────────────────────────────────────────────────────────────


def test_missing_source_gets_penalty_and_present_false() -> None:
    stats = by_text(rank_stats([ok("google_suggest", "가", "나"), ok("youtube_suggest", "가")]))
    yt = next(r for r in stats["나"].per_source if r.source == "youtube_suggest")
    assert yt.present is False
    assert yt.pct_rank == MISSING_PCT_RANK
    assert stats["나"].present_count == 1


def test_lists_of_different_length_are_comparable() -> None:
    """소스마다 길이가 달라도 1위끼리는 백분위로 비교 가능해야 한다."""
    stats = by_text(
        rank_stats(
            [ok("naver_autocomplete", "가", "나"), ok("google_suggest", "가", "다", "라", "마")]
        )
    )
    ranks = {r.source: r.pct_rank for r in stats["가"].per_source}
    assert ranks["naver_autocomplete"] == pytest.approx(1 / 3)
    assert ranks["google_suggest"] == pytest.approx(1 / 5)


def test_std_ignores_missing_penalty() -> None:
    """결측 1.0은 대입값 — 분산에 들어가면 '불일치'가 아니라 '결측 수'를 잰다."""
    stats = by_text(
        rank_stats(
            [
                ok("naver_autocomplete", "다", "라"),
                ok("google_suggest", "가", "나"),
                ok("youtube_suggest", "나", "가"),
            ]
        )
    )
    # '가': google 1/3, youtube 2/3, naver 결측. 관측 두 개만으로 재야 한다.
    assert stats["가"].rank_std == pytest.approx(pstdev([1 / 3, 2 / 3]))
    assert stats["가"].rank_std != pytest.approx(pstdev([1 / 3, 2 / 3, 1.0]))


def test_single_source_std_is_undefined_not_zero() -> None:
    """'불일치가 없다'(0.0)와 '불일치를 잴 수 없다'(None)는 다른 사실이다."""
    stats = rank_stats([ok("google_suggest", "가", "나")])
    assert [s.rank_std for s in stats] == [None, None]


def test_two_source_and_one_source_do_not_collide() -> None:
    """실측 회귀: 대입값을 분산에 넣던 판에서 2소스 후보와 1소스 후보가 같은 std를 받았다."""
    stats = by_text(
        rank_stats(
            [
                ok("naver_autocomplete", "무관1", "무관2", "무관3"),
                ok("google_suggest", "무관4", "둘다", "무관5"),
                ok("youtube_suggest", "둘다", "하나만", "무관6"),
            ]
        )
    )
    assert stats["둘다"].present_count == 2 and stats["둘다"].rank_std is not None
    assert stats["하나만"].present_count == 1 and stats["하나만"].rank_std is None


def test_three_agreeing_sources_yield_zero_std() -> None:
    stats = by_text(
        rank_stats(
            [
                ok("naver_autocomplete", "가", "나"),
                ok("google_suggest", "가", "나"),
                ok("youtube_suggest", "가", "나"),
            ]
        )
    )
    assert stats["가"].rank_std == pytest.approx(0.0)
    assert stats["가"].present_count == 3


def test_observed_mean_excludes_imputed_values() -> None:
    stats = by_text(rank_stats([ok("google_suggest", "가"), ok("youtube_suggest", "나")]))
    assert stats["가"].observed_mean == pytest.approx(0.5)


def test_failed_source_is_excluded_not_penalised() -> None:
    """장애 소스를 결측으로 채우면 통계가 장애의 함수가 된다(FR-G4 격리의 취지에 반한다)."""
    stats = rank_stats([ok("google_suggest", "가", "나"), fail("youtube_suggest")])
    assert all(len(s.per_source) == 1 for s in stats)


def test_empty_observation_participates() -> None:
    """성공했지만 항목 0개인 소스는 '관측했고 없었다' — 참여시킨다."""
    stats = by_text(rank_stats([ok("google_suggest", "가"), ok("youtube_suggest")]))
    yt = next(r for r in stats["가"].per_source if r.source == "youtube_suggest")
    assert yt.present is False


def test_all_sources_failed_yields_nothing() -> None:
    assert rank_stats([fail("google_suggest"), fail("youtube_suggest")]) == ()


def test_whitespace_variants_merge_into_one_candidate() -> None:
    stats = rank_stats([ok("google_suggest", "개발자 연봉"), ok("youtube_suggest", "개발자연봉")])
    assert len(stats) == 1
    assert stats[0].present_count == 2


def test_duplicate_within_source_keeps_first_rank() -> None:
    stats = by_text(rank_stats([ok("google_suggest", "가", "가 ", "나")]))
    assert stats["가"].per_source[0].pct_rank == pytest.approx(0.25)
    assert stats["나"].per_source[0].pct_rank == pytest.approx(0.75)


def test_sorted_by_evidence_then_observed_rank() -> None:
    """독립 관측 수가 먼저다 — 단독 소스 1위가 2소스 후보를 앞지르지 않는다."""
    stats = rank_stats(
        [
            ok("naver_autocomplete", "단독1위", "둘다"),
            ok("google_suggest", "둘다", "무관"),
            ok("youtube_suggest", "무관2", "무관3"),
        ]
    )
    order = [s.text for s in stats]
    assert order.index("둘다") < order.index("단독1위")


def test_keyword_stat_key_is_squeezed() -> None:
    stat = KeywordStat(
        text="개발자 연봉",
        present_count=1,
        observed_mean=0.1,
        rank_std=None,
        per_source=(SourceRank("google_suggest", 0.1, True),),
    )
    assert stat.key == "개발자연봉"


# ── 퍼센타일 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(("p", "expected"), [(0, 1.0), (25, 2.0), (50, 3.0), (75, 4.0), (100, 5.0)])
def test_percentile_linear_interpolation(p: float, expected: float) -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], p) == pytest.approx(expected)


def test_percentile_interpolates_between_points() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 25) == pytest.approx(1.75)


def test_percentile_ignores_input_order() -> None:
    assert percentile([5.0, 1.0, 3.0], 50) == pytest.approx(3.0)


def test_percentile_rejects_empty() -> None:
    with pytest.raises(ValueError, match="빈 표본"):
        percentile([], 50)


@pytest.mark.parametrize("p", [-1.0, 101.0])
def test_percentile_rejects_out_of_range(p: float) -> None:
    with pytest.raises(ValueError, match="0~100"):
        percentile([1.0, 2.0], p)


def test_band_bounds_rejects_inverted() -> None:
    with pytest.raises(ValueError, match="뒤집"):
        band_bounds([1.0, 2.0], (75, 25))


# ── 제외 키워드 매칭 ──────────────────────────────────────────────────


def test_exclude_respects_word_spacing() -> None:
    """공백의 양은 무시하되 존재는 존중한다 — 공백을 지우면 없던 말이 생긴다."""
    assert excluded_by("리콜  대상 확인", ["리콜"]) == "리콜"
    assert excluded_by("최고 장점", ["고장"]) is None


def test_exclude_is_case_insensitive() -> None:
    assert excluded_by("AI 개발자", ["ai"]) == "ai"


def test_exclude_returns_none_when_clean() -> None:
    assert excluded_by("개발자 연봉", ["리콜", "결함"]) is None
