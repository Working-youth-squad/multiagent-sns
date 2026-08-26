"""research_trends 오케스트레이션 검증 — 실패/타임아웃 격리 + 다이제스트 (FR-G4)."""

import time

from sns.research.trends import (
    ENV_GEMINI_API_KEY,
    ENV_NAVER_CLIENT_ID,
    ENV_NAVER_CLIENT_SECRET,
    ENV_YOUTUBE_API_KEY,
    ResearchTrendsService,
    default_service,
)
from sns.tools.contracts import TrendDigest


def _ok(*items: str):
    # 서비스의 정규화(공백 제거·limit 상한)를 독립 검증하려고 limit을 무시하고 전부 반환.
    def _fetch(limit: int) -> tuple[str, ...]:
        return items

    return _fetch


def _boom(limit: int) -> tuple[str, ...]:
    raise RuntimeError("source down")


def test_aggregates_ok_sources() -> None:
    svc = ResearchTrendsService({"a": _ok("x", "y"), "b": _ok("z")})
    digest = svc()
    assert isinstance(digest, TrendDigest)
    by = {r.source: r for r in digest.source_results}
    assert by["a"].ok and by["a"].items == ("x", "y")
    assert by["b"].ok and by["b"].items == ("z",)


def test_failed_source_isolated_and_excluded_from_digest() -> None:
    svc = ResearchTrendsService({"good": _ok("keep"), "bad": _boom})
    digest = svc()
    by = {r.source: r for r in digest.source_results}
    assert by["good"].ok
    assert not by["bad"].ok and by["bad"].items == ()
    # 확인된 소스만 다이제스트에 — 실패 소스 헤더는 없다.
    assert "keep" in digest.digest_markdown
    assert "## bad" not in digest.digest_markdown
    # source_results는 순서 보존하며 둘 다 싣는다(관측용).
    assert [r.source for r in digest.source_results] == ["good", "bad"]


def test_unregistered_source_is_ok_false() -> None:
    svc = ResearchTrendsService({"a": _ok("x")})
    digest = svc(sources=("a", "ghost"))
    by = {r.source: r for r in digest.source_results}
    assert by["a"].ok
    assert not by["ghost"].ok


def test_timeout_isolates_slow_source() -> None:
    def slow(limit: int) -> tuple[str, ...]:
        time.sleep(0.5)
        return ("late",)

    svc = ResearchTrendsService({"fast": _ok("quick"), "slow": slow}, timeout_s=0.05)
    digest = svc()
    by = {r.source: r for r in digest.source_results}
    assert by["fast"].ok and by["fast"].items == ("quick",)
    assert not by["slow"].ok  # 타임아웃 → 격리, 전체는 계속


def test_limit_truncates_and_cleans() -> None:
    svc = ResearchTrendsService({"a": _ok("  x  ", "", "y", "z")})
    digest = svc(limit=2)
    assert digest.source_results[0].items == ("x", "y")  # 공백 정리 → 상한 2


def test_empty_registry() -> None:
    digest = ResearchTrendsService({})()
    assert digest.source_results == ()
    assert digest.digest_markdown.strip() == "# 트렌드 다이제스트"


def test_digest_sections_follow_selected_order() -> None:
    svc = ResearchTrendsService({"a": _ok("a1"), "b": _ok("b1")})
    md = svc(sources=("b", "a")).digest_markdown
    assert md.index("## b") < md.index("## a")


def test_default_service_isolates_unimplemented_sources() -> None:
    # 키 없는 env에선 인증 소스가 미배선 → 네트워크 접촉 없이 ok=False로 격리된다.
    digest = default_service(env={})(sources=("naver_search",))
    assert digest.source_results[0].source == "naver_search"
    assert not digest.source_results[0].ok


def test_default_service_only_unauthed_without_keys() -> None:
    svc = default_service(env={})
    assert set(svc.sources) == {
        "google_trends",
        "github_trending",
        "hacker_news",
        "lobsters",
    }


def test_default_service_registers_naver_pair_with_both_creds() -> None:
    svc = default_service(env={ENV_NAVER_CLIENT_ID: "i", ENV_NAVER_CLIENT_SECRET: "s"})
    assert {"naver_search", "naver_datalab"} <= set(svc.sources)


def test_default_service_naver_needs_both_halves() -> None:
    # id만 있고 secret이 없으면 등록하지 않는다(반쪽 자격증명 방어).
    svc = default_service(env={ENV_NAVER_CLIENT_ID: "i"})
    assert "naver_search" not in svc._fetchers
    assert "naver_datalab" not in svc._fetchers


def test_default_service_registers_youtube_and_llm_with_keys() -> None:
    svc = default_service(env={ENV_YOUTUBE_API_KEY: "y", ENV_GEMINI_API_KEY: "g"})
    assert "youtube_popular" in svc._fetchers
    assert "llm_grounding" in svc._fetchers


# ── 온보딩 채널 주입 (프로필 맞춤 트렌드) ──────────────────────────────
# 소스만 갈아끼우고 질의어를 그대로 두면 도메인을 바꿔도 같은 걸 검색한다 —
# 실제로 요리 채널이 "엔비디아 실적"을 골라 야식으로 비틀었다.

_ALL_KEYS = {
    ENV_NAVER_CLIENT_ID: "i",
    ENV_NAVER_CLIENT_SECRET: "s",
    ENV_YOUTUBE_API_KEY: "y",
    ENV_GEMINI_API_KEY: "g",
}


def test_sources_narrow_the_registry() -> None:
    """run_topic은 소스를 지정하지 않고 부른다 — 등록 시점에 걸러야 한다."""
    svc = default_service(env=_ALL_KEYS, sources=("google_trends", "naver_search"))
    assert set(svc.sources) == {"google_trends", "naver_search"}


def test_sources_none_keeps_everything() -> None:
    """기본값은 기존 동작 무변경."""
    svc = default_service(env=_ALL_KEYS)
    assert "hacker_news" in svc.sources and "github_trending" in svc.sources


def test_search_terms_bind_to_naver_fetchers(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """첫 항목이 검색 대표 질의어, 전체가 데이터랩 추이 키워드."""
    seen: dict[str, object] = {}

    def fake_search(limit: int, **kw: object) -> tuple[str, ...]:
        seen["query"] = kw.get("query")
        return ()

    def fake_datalab(limit: int, **kw: object) -> tuple[str, ...]:
        seen["keywords"] = kw.get("keywords")
        return ()

    monkeypatch.setattr("sns.research.sources.naver_search.fetch_naver_search", fake_search)
    monkeypatch.setattr("sns.research.sources.naver_datalab.fetch_naver_datalab", fake_datalab)

    svc = default_service(env=_ALL_KEYS, search_terms=("요리", "자취요리", "간편식"))
    svc(sources=("naver_search", "naver_datalab"))
    assert seen["query"] == "요리"
    assert seen["keywords"] == ("요리", "자취요리", "간편식")


def test_no_search_terms_leaves_fetcher_defaults(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """안 넘기면 fetcher 기본값이 쓰인다 — 바인딩에 빈 값을 밀어넣지 않는다."""
    seen: dict[str, object] = {}

    def fake_search(limit: int, **kw: object) -> tuple[str, ...]:
        seen["kw"] = kw
        return ()

    monkeypatch.setattr("sns.research.sources.naver_search.fetch_naver_search", fake_search)
    default_service(env=_ALL_KEYS)(sources=("naver_search",))
    assert "query" not in seen["kw"]  # type: ignore[operator]


def test_grounding_prompt_binds(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, object] = {}

    def fake_grounding(limit: int, **kw: object) -> tuple[str, ...]:
        seen["prompt"] = kw.get("prompt")
        return ()

    monkeypatch.setattr("sns.research.sources.llm_grounding.fetch_llm_grounding", fake_grounding)
    default_service(env=_ALL_KEYS, grounding_prompt="요리 주제를 나열해줘")(
        sources=("llm_grounding",)
    )
    assert seen["prompt"] == "요리 주제를 나열해줘"
