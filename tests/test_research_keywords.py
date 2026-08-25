"""질의어 기반 배선 — 토글 4종, 밴드 판정, 실패 격리, JSON 직렬화 (04-트렌드조사 §5).

네트워크는 건드리지 않는다: `rank_keywords`에 `service=`로 가짜 서비스를 주입한다
(`trends`가 세운 주입 규율 그대로).
"""

import pytest

from sns.research.keywords import (
    KEYWORD_SOURCES,
    aggregate,
    keyword_service,
    rank_keywords,
    ranking_to_dict,
)
from sns.research.trends import ResearchTrendsService, SourceFetcher

# rank_std는 **관측 소스가 2개 이상**일 때만 정의되므로, 밴드가 실제로 열리려면 교차 등장
# 후보가 MIN_BAND_POOL(4) 이상 있어야 한다. 아래 등수 배치는 그렇게 설계한 것이라
# 밴드 양쪽 꼬리가 모두 잘린다: 완전 합의 2건(하위 컷) · 크게 엇갈린 2건(상위 컷) · 중간 4건 통과.
NAVER = (
    "개발자 연봉",  # 3소스 모두 1위 — 완전 합의
    "개발자 취업",
    "개발자 되는법",  # 3소스 모두 3위 — 완전 합의
    "개발자 로드맵",
    "개발자 이력서",
    "개발자 이직",
    "개발자 포트폴리오",
    "개발자 면접",
    "개발자 자격증",  # naver 단독
    "개발자모드",  # naver 단독
)
GOOGLE = (
    "개발자 연봉",
    "개발자 로드맵",
    "개발자 되는법",
    "개발자 취업",
    "개발자 이직",
    "개발자 면접",
    "개발자 노트북",  # google 단독
    "개발자 포트폴리오",
    "개발자 이력서",
    "개발자 커뮤니티",  # google 단독
)
YOUTUBE = (
    "개발자 연봉",
    "개발자 이력서",
    "개발자 되는법",
    "개발자 포트폴리오",
    "개발자 로드맵",
    "개발자 짤",  # youtube 단독
    "개발자 이직",
    "개발자 취업",
    "개발자 브이로그",  # youtube 단독
    "개발자 면접",
)


def fetcher(*items: str) -> SourceFetcher:
    def fetch(limit: int) -> tuple[str, ...]:
        return items[:limit]

    return fetch


def boom(limit: int) -> tuple[str, ...]:
    raise RuntimeError("소스 장애")


def fake_service(**overrides: SourceFetcher) -> ResearchTrendsService:
    fetchers: dict[str, SourceFetcher] = {
        "naver_autocomplete": fetcher(*NAVER),
        "google_suggest": fetcher(*GOOGLE),
        "youtube_suggest": fetcher(*YOUTUBE),
    }
    fetchers.update(overrides)
    return ResearchTrendsService(fetchers)


def run(**kwargs: object) -> object:
    kwargs.setdefault("service", fake_service())
    kwargs.setdefault("limit", 20)
    return rank_keywords("개발자", **kwargs)  # type: ignore[arg-type]


# ── 기본 경로 ────────────────────────────────────────────────────────


def test_default_uses_three_autocomplete_sources() -> None:
    assert run().sources_ok == KEYWORD_SOURCES  # type: ignore[attr-defined]


def test_band_opens_and_reports_bounds() -> None:
    r = run()
    assert r.filter_mode == "active"  # type: ignore[attr-defined]
    assert r.band is not None  # type: ignore[attr-defined]


def test_consensus_keyword_is_cut_as_lower_tail() -> None:
    """3소스가 같은 등수로 말하는 키워드 → rank_std 0.0 → 하위 꼬리."""
    r = run(top=99)
    assert "개발자 연봉" in {s.text for s in r.dropped}  # type: ignore[attr-defined]
    consensus = next(s for s in r.pool if s.text == "개발자 연봉")  # type: ignore[attr-defined]
    assert consensus.rank_std == 0.0 and consensus.present_count == 3


def test_single_source_keyword_is_unscored_not_dropped() -> None:
    """유튜브에만 있는 키워드는 소스 간 불일치를 잴 수 없다 — 판정 대상이 아니다."""
    r = run(top=99)
    assert "개발자 짤" in {s.text for s in r.unscored}  # type: ignore[attr-defined]
    assert "개발자 짤" not in {s.text for s in r.dropped}  # type: ignore[attr-defined]
    assert all(s.rank_std is not None for s in r.dropped)  # type: ignore[attr-defined]


def test_cross_validated_candidates_rank_first() -> None:
    r = run(band=False, top=99)
    counts = [s.present_count for s in r.candidates]  # type: ignore[attr-defined]
    assert counts == sorted(counts, reverse=True)


def test_kept_and_dropped_partition_the_pool() -> None:
    r = run(top=99)
    assert len(r.candidates) + len(r.dropped) == len(r.pool)  # type: ignore[attr-defined]


# ── 토글 ① 소스별 ────────────────────────────────────────────────────


def test_source_toggle_limits_collection() -> None:
    r = run(sources=("google_suggest",))
    assert r.sources_ok == ("google_suggest",)  # type: ignore[attr-defined]
    # 소스 1개면 어떤 후보도 rank_std를 가질 수 없다.
    assert r.filter_mode == "passthrough"  # type: ignore[attr-defined]
    assert "참여 소스" in r.reason  # type: ignore[attr-defined]


def test_one_dead_source_does_not_stop_the_cycle() -> None:
    r = run(service=fake_service(youtube_suggest=boom))
    assert r.sources_failed == ("youtube_suggest",)  # type: ignore[attr-defined]
    assert len(r.sources_ok) == 2 and r.candidates  # type: ignore[attr-defined]


# ── 토글 ② 밴드 ──────────────────────────────────────────────────────


def test_band_off_returns_everything() -> None:
    r = run(band=False, top=99)
    assert r.filter_mode == "off"  # type: ignore[attr-defined]
    assert len(r.candidates) == len(r.pool) and r.dropped == ()  # type: ignore[attr-defined]


def test_custom_percentiles_widen_the_band() -> None:
    narrow = run(percentiles=(40.0, 60.0), top=99)
    wide = run(percentiles=(0.0, 100.0), top=99)
    assert len(wide.candidates) > len(narrow.candidates)  # type: ignore[attr-defined]


def test_top_caps_the_candidate_list() -> None:
    assert len(run(percentiles=(0.0, 100.0), top=2).candidates) == 2  # type: ignore[attr-defined]


def test_thin_scored_pool_is_passthrough_not_active() -> None:
    """정의된 후보가 모자라면 필터를 열 수 없다 — 그 사실을 reason에 적는다."""
    thin = ResearchTrendsService(
        {
            "naver_autocomplete": fetcher("가", "나", "다"),
            "google_suggest": fetcher("라", "마", "바"),
            "youtube_suggest": fetcher("사", "아", "자"),
        }
    )
    r = rank_keywords("개발자", service=thin, top=99)
    assert r.filter_mode == "passthrough"
    assert "정의된 후보" in r.reason


# ── 토글 ③ 교차검증 하한 ─────────────────────────────────────────────


def test_min_present_defaults_to_no_filtering() -> None:
    """기본값이 후보를 줄이면 기존 호출자의 결과가 조용히 바뀐다."""
    assert any(s.present_count == 1 for s in run(band=False, top=99).pool)  # type: ignore[attr-defined]


def test_min_present_two_keeps_only_cross_validated() -> None:
    r = run(min_present=2, band=False, top=99)
    assert all(s.present_count >= 2 for s in r.candidates)  # type: ignore[attr-defined]
    assert "개발자 짤" not in {s.text for s in r.pool}  # type: ignore[attr-defined]


def test_min_present_three_demands_full_agreement() -> None:
    r = run(min_present=3, band=False, top=99)
    texts = {s.text for s in r.candidates}  # type: ignore[attr-defined]
    assert texts == set(NAVER) & set(GOOGLE) & set(YOUTUBE)


def test_min_present_below_one_rejected() -> None:
    with pytest.raises(ValueError, match="min_present"):
        run(min_present=0)


# ── 토글 ④ 제외 키워드 ───────────────────────────────────────────────


def test_no_exclusion_by_default() -> None:
    """무엇이 부적절한지는 도메인이 정한다 — 이 모듈이 임의로 거르지 않는다."""
    assert run(band=False, top=99).excluded == ()  # type: ignore[attr-defined]


def test_injected_exclusions_remove_candidates() -> None:
    r = run(exclude=("짤",), band=False, top=99)
    assert "개발자 짤" not in {s.text for s in r.candidates}  # type: ignore[attr-defined]
    assert ("개발자 짤", "짤") in r.excluded  # type: ignore[attr-defined]


# ── 순수성·직렬화·배선 ───────────────────────────────────────────────


def test_aggregate_is_deterministic() -> None:
    results = fake_service()(KEYWORD_SOURCES, limit=20).source_results
    assert aggregate("개발자", results) == aggregate("개발자", results)


def test_undefined_std_serialises_as_null() -> None:
    """JSON 경계에서 None이 0.0으로 뭉개지면 '불일치 없음'으로 오독된다."""
    payload = ranking_to_dict(run(band=False, top=99))  # type: ignore[arg-type]
    single = next(c for c in payload["candidates"] if c["present_count"] == 1)  # type: ignore[index]
    assert single["rank_std"] is None


def test_ranking_to_dict_shape() -> None:
    payload = ranking_to_dict(run())  # type: ignore[arg-type]
    assert set(payload) >= {
        "query",
        "filter_mode",
        "band",
        "reason",
        "sources_ok",
        "sources_failed",
        "candidates",
        "dropped",
        "unscored",
        "excluded",
    }


def test_keyword_service_registers_three_unauthenticated_sources() -> None:
    """자동완성 3종은 무인증 — 키 없이 항상 등록된다."""
    assert set(keyword_service("개발자").sources) == set(KEYWORD_SOURCES)


def test_keyword_service_rejects_blank_query() -> None:
    with pytest.raises(ValueError, match="비어 있다"):
        keyword_service("   ")


# ── CLI 렌더 ─────────────────────────────────────────────────────────


def test_render_lists_every_dropped_candidate() -> None:
    """하위 꼬리의 rank_std는 정확히 0.0 — 참/거짓으로 거르면 통째로 사라진다."""
    from scripts.rank_keywords import render

    r = run(top=99)
    text = render(r)  # type: ignore[arg-type]
    assert f"밴드 밖 {len(r.dropped)}건" in text  # type: ignore[attr-defined]
    for s in r.dropped:  # type: ignore[attr-defined]
        assert s.text in text


def test_render_marks_undefined_std() -> None:
    from scripts.rank_keywords import render

    assert "미정의" in render(run(band=False, top=99))  # type: ignore[arg-type]
