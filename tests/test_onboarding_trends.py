"""프로필 → 트렌드 서비스 조립 — 온보딩 추천과 사이클이 같은 조립을 쓴다.

예전엔 `default_trend_provider`가 `profile`을 인자로 받아놓고 **쓰지 않았다.** 그래서
요리 채널로 인터뷰해도 화면 6의 추천이 개발 트렌드(hacker_news·github)를 근거로 나왔다.
사이클 쪽만 고쳐두면 같은 결함이 온보딩 화면에 남는다.
"""

from collections.abc import Mapping

from sns.onboarding.profile import parse_profile
from sns.onboarding.trends import profile_trend_service
from sns.research.trends import (
    ENV_GEMINI_API_KEY,
    ENV_NAVER_CLIENT_ID,
    ENV_NAVER_CLIENT_SECRET,
    ENV_YOUTUBE_API_KEY,
)

# 인증 소스를 전부 켜야 파생이 고른 소스가 실제로 등록되는지 볼 수 있다.
FULL_ENV: Mapping[str, str] = {
    ENV_NAVER_CLIENT_ID: "id",
    ENV_NAVER_CLIENT_SECRET: "secret",
    ENV_YOUTUBE_API_KEY: "yt",
    ENV_GEMINI_API_KEY: "gemini",
}
DEV_ONLY = {"github_trending", "hacker_news", "lobsters"}


def _profile(**overrides: object):  # type: ignore[no-untyped-def]
    base: dict[str, object] = {
        "topic_major": "개발",
        "topic_subs": ["AI", "파이썬"],
        "tone": "casual",
        "goal_ref": "engagement_depth",
        "character": {"style": "none"},
    }
    base.update(overrides)
    return parse_profile(base)


def test_dev_profile_keeps_every_source() -> None:
    """이사가 개발 채널 동작을 바꾸지 않았다는 증거."""
    svc = profile_trend_service(_profile(), env=FULL_ENV)
    assert DEV_ONLY <= set(svc.sources)
    assert {"google_trends", "youtube_popular", "llm_grounding"} <= set(svc.sources)


def test_non_dev_profile_drops_developer_sources() -> None:
    """요리 채널이 개발 뉴스를 근거로 추천받으면 채널 이름이 엉뚱해진다."""
    svc = profile_trend_service(_profile(topic_major="요리", topic_subs=["자취요리"]), env=FULL_ENV)
    assert not (DEV_ONLY & set(svc.sources))
    assert "google_trends" in svc.sources  # 일반 급상승은 모든 채널이 본다


def test_search_terms_come_from_the_profile(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """대분류가 대표 질의어, 세부까지가 추이 키워드 — 사람이 인터뷰에서 고른 말이다."""
    seen: dict[str, object] = {}

    def fake_search(limit: int, **kw: object) -> tuple[str, ...]:
        seen["query"] = kw.get("query")
        return ()

    def fake_datalab(limit: int, **kw: object) -> tuple[str, ...]:
        seen["keywords"] = kw.get("keywords")
        return ()

    monkeypatch.setattr("sns.research.sources.naver_search.fetch_naver_search", fake_search)
    monkeypatch.setattr("sns.research.sources.naver_datalab.fetch_naver_datalab", fake_datalab)

    profile = _profile(topic_major="요리", topic_subs=["자취요리", "간편식"])
    svc = profile_trend_service(profile, env=FULL_ENV)
    svc(sources=("naver_search", "naver_datalab"))
    assert seen["query"] == "요리"
    assert seen["keywords"] == ("요리", "자취요리", "간편식")


def test_grounding_prompt_comes_from_the_profile(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, object] = {}

    def fake_grounding(limit: int, **kw: object) -> tuple[str, ...]:
        seen["prompt"] = kw.get("prompt")
        return ()

    monkeypatch.setattr("sns.research.sources.llm_grounding.fetch_llm_grounding", fake_grounding)
    profile = _profile(topic_major="요리", topic_subs=["자취요리"])
    profile_trend_service(profile, env=FULL_ENV)(sources=("llm_grounding",))
    prompt = str(seen["prompt"])
    assert "요리" in prompt and "자취요리" in prompt
    assert "개발" not in prompt
