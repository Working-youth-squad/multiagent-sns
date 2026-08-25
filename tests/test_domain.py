"""도메인 팩 계약 — 주제 도메인이 바뀔 때 갈아끼우는 값들이 실재하는지 본다.

팩은 문자열 뭉치라 오타가 조용히 지나간다. 없는 개념 그림 종류를 적어두면 렌더 시점에야
터지고, 없는 트렌드 소스를 적어두면 그 소스만 조용히 빠진 채 사이클이 돈다. 그래서
**팩이 가리키는 이름이 전부 실재하는지**를 여기서 강제한다.
"""

from dataclasses import replace

import pytest

from sns.domain import DEVELOPER, Domain, UnknownDomainError, resolve_domain
from sns.render.concept_image import CONCEPT_FIELDS
from sns.research.trends import (
    ENV_GEMINI_API_KEY,
    ENV_NAVER_CLIENT_ID,
    ENV_NAVER_CLIENT_SECRET,
    ENV_YOUTUBE_API_KEY,
    default_service,
)

# 모든 인증 소스가 배선된 상태 — 팩이 어떤 소스를 적든 해소되는지 보려면 전부 켜야 한다.
FULL_ENV = {
    ENV_NAVER_CLIENT_ID: "id",
    ENV_NAVER_CLIENT_SECRET: "secret",
    ENV_YOUTUBE_API_KEY: "yt",
    ENV_GEMINI_API_KEY: "gemini",
}

ALL_DOMAINS: tuple[Domain, ...] = (DEVELOPER,)


@pytest.mark.parametrize("domain", ALL_DOMAINS, ids=lambda d: d.ref)
def test_concept_kinds_exist_in_the_renderer(domain: Domain) -> None:
    """팩이 없는 개념 그림 종류를 적으면 렌더 시점에야 터진다 — 여기서 잡는다."""
    unknown = set(domain.concept_kinds) - set(CONCEPT_FIELDS)
    assert not unknown, f"{domain.ref}: 렌더러가 모르는 개념 그림 {sorted(unknown)}"


@pytest.mark.parametrize("domain", ALL_DOMAINS, ids=lambda d: d.ref)
def test_every_concept_kind_has_a_prompt_example(domain: Domain) -> None:
    """예시 없는 종류를 허용하면 프롬프트가 그 종류를 설명하지 못한 채 열어준다."""
    assert set(domain.concept_examples) == set(domain.concept_kinds)


@pytest.mark.parametrize("domain", ALL_DOMAINS, ids=lambda d: d.ref)
def test_trend_sources_are_wired(domain: Domain) -> None:
    """팩이 없는 소스를 적으면 그 소스만 조용히 빠진 채 사이클이 돈다."""
    wired = set(default_service(env=FULL_ENV).sources)
    unknown = set(domain.trend_sources) - wired
    assert not unknown, f"{domain.ref}: 배선되지 않은 트렌드 소스 {sorted(unknown)}"


@pytest.mark.parametrize("domain", ALL_DOMAINS, ids=lambda d: d.ref)
def test_search_terms_are_present(domain: Domain) -> None:
    """질의어가 비면 네이버 소스가 무엇을 검색할지 알 수 없다."""
    assert domain.search_terms
    assert all(t.strip() for t in domain.search_terms)


@pytest.mark.parametrize("domain", ALL_DOMAINS, ids=lambda d: d.ref)
def test_categories_and_audience_are_present(domain: Domain) -> None:
    """카테고리가 비면 Topic 에이전트가 고를 축이 사라진다."""
    assert domain.categories
    assert all(c.strip() for c in domain.categories)
    assert domain.audience.strip()


def test_resolve_domain_finds_the_pack_by_ref() -> None:
    assert resolve_domain("developer") is DEVELOPER


def test_resolve_domain_rejects_unknown_ref() -> None:
    """조용히 기본값으로 떨어지면 엉뚱한 도메인으로 한 사이클이 돈다."""
    with pytest.raises(UnknownDomainError):
        resolve_domain("nonexistent")


# ── 팩이 실제로 쓰이는가 ────────────────────────────────────────────
# 위 계약이 맞아도 호출부가 팩을 안 읽으면 아무 의미가 없다. 개발자 도메인과 겹치지
# 않는 가짜 팩을 넣고, 산출물에 그 값이 나오는지로 본다.

FAKE = Domain(
    ref="fake",
    audience="정원사 대상",
    topic_domain="정원 가꾸기",
    categories=("흙", "물주기"),
    grounding_prompt="정원 관련 주제를 나열해줘.",
    trend_sources=("google_trends",),
    search_terms=("분갈이", "화분", "다육식물"),
    concept_kinds=("emphasis",),
    concept_examples={"emphasis": '       - 수치 한 방: {"kind":"emphasis",...} 화분 12개'},
    square_guidance=(
        "     * photo(선택): 화분 사진 한 장.\n     * concept(선택): 종류 «N»개.\n«EXAMPLES»\n"
    ),
)


def test_topic_prompt_is_built_from_the_pack() -> None:
    from sns.agents.topic import _system_prompt

    prompt = _system_prompt(FAKE)
    assert "정원사 대상" in prompt
    assert "흙" in prompt and "물주기" in prompt
    assert "개발자" not in prompt, "팩 밖의 도메인 문구가 남아 있다"


def test_content_prompt_documents_only_pack_concept_kinds() -> None:
    from sns.agents.content import _system_prompt

    prompt = _system_prompt(FAKE)
    assert "화분 12개" in prompt, "팩이 준 예시가 프롬프트에 없다"
    assert "terminal" not in prompt, "팩이 안 쓰는 종류를 프롬프트가 열어줬다"


def test_parse_concept_rejects_kind_outside_the_pack() -> None:
    from sns.render.concept_image import ConceptError, parse_concept

    raw = {"kind": "terminal", "commands": ["pip install foo"], "note": "설명"}
    with pytest.raises(ConceptError):
        parse_concept(raw, kinds=FAKE.concept_kinds)


def test_parse_concept_still_accepts_kinds_the_pack_allows() -> None:
    from sns.render.concept_image import parse_concept

    raw = {"kind": "emphasis", "tag": "태그", "headline": "100억", "sub": "부연"}
    assert parse_concept(raw, kinds=FAKE.concept_kinds).kind == "emphasis"


def test_default_service_wires_only_pack_sources() -> None:
    """키가 다 있어도 팩이 안 쓰는 소스는 배선하지 않는다."""
    assert set(default_service(env=FULL_ENV, domain=FAKE).sources) == {"google_trends"}


def test_naver_sources_search_the_pack_terms() -> None:
    """소스 목록만 팩에서 오고 질의어가 하드코딩이면, 도메인을 바꿔도 "개발자"를 검색한다."""
    import json
    import urllib.parse

    from sns.research.sources.naver_datalab import fetch_naver_datalab
    from sns.research.sources.naver_search import fetch_naver_search
    from tests.test_research_sources import _DATALAB, _NAVER_NEWS, _opener

    news: dict[str, object] = {}
    fetch_naver_search(
        2,
        client_id="i",
        client_secret="s",
        query=FAKE.search_terms[0],
        opener=_opener(_NAVER_NEWS, news),
    )
    sent = urllib.parse.unquote(news["target"].full_url)  # type: ignore[attr-defined]
    assert FAKE.search_terms[0] in sent

    lab: dict[str, object] = {}
    fetch_naver_datalab(
        2,
        client_id="i",
        client_secret="s",
        keywords=FAKE.search_terms,
        opener=_opener(_DATALAB, lab),
    )
    body = json.loads(lab["target"].data)  # type: ignore[attr-defined]
    groups = [g["groupName"] for g in body["keywordGroups"]]
    assert set(groups) == set(FAKE.search_terms)


def test_default_service_binds_pack_search_terms_to_naver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """배선 지점에서 팩 질의어가 실제로 묶이는지 — 여기가 안 되면 팩 필드가 장식이다."""
    import sns.research.sources.naver_datalab as datalab
    import sns.research.sources.naver_search as search

    seen: dict[str, object] = {}

    def spy_search(limit: int, **kw: object) -> tuple[str, ...]:
        seen["query"] = kw.get("query")
        return ()

    def spy_datalab(limit: int, **kw: object) -> tuple[str, ...]:
        seen["keywords"] = kw.get("keywords")
        return ()

    monkeypatch.setattr(search, "fetch_naver_search", spy_search)
    monkeypatch.setattr(datalab, "fetch_naver_datalab", spy_datalab)

    pack = replace(DEVELOPER, search_terms=("분갈이", "화분"))
    default_service(env=FULL_ENV, domain=pack)(sources=("naver_search", "naver_datalab"))

    assert seen["query"] == "분갈이"
    assert seen["keywords"] == ("분갈이", "화분")


def test_content_prompt_uses_pack_square_guidance() -> None:
    """정사각 안내는 도메인마다 다르다 — 코드가 없는 도메인에 code 안내는 잡음이다."""
    from sns.agents.content import _system_prompt

    prompt = _system_prompt(FAKE)
    assert "화분 사진 한 장" in prompt
    assert "pygments" not in prompt, "팩 밖의 정사각 안내가 남아 있다"
    assert "focus_lines" not in prompt


def test_run_cycle_passes_the_pack_to_the_agents() -> None:
    """러너가 팩을 안 넘기면 팩의 카테고리가 거부되어 주제 확정 자체가 실패한다.

    가짜 팩의 "흙"은 개발자 팩에 없는 카테고리라, `choose_topic`이 기본 팩으로 검증하면
    거부하고 사이클이 TopicSelectionError로 죽는다 — 전달 여부가 결과로 드러난다.
    """
    from sns.runner.cycle import run_cycle
    from sns.runner.store import InMemoryCycleStore
    from sns.tools.fakes import FakeReadStats, FakeRenderMedia, FakeResearchTrends
    from tests.test_cycle_runner import ScriptedChatModel as Scripted
    from tests.test_cycle_runner import (
        _content_script,
        _passing_quality,
        _target,
        _topic_script,
    )

    script = _topic_script(category="흙") + _content_script()
    result = run_cycle(
        InMemoryCycleStore(),
        goal_ref="engagement_depth",
        targets=[_target()],
        model=Scripted(messages=iter(script)),
        research_trends=FakeResearchTrends(),
        read_stats=FakeReadStats(),
        render_media=FakeRenderMedia(),
        assess_quality=_passing_quality,
        domain=FAKE,
    )
    assert result.status == "completed"


def test_llm_grounding_sends_the_pack_prompt() -> None:
    import json

    from sns.research.sources.llm_grounding import fetch_llm_grounding
    from tests.test_research_sources import _GEMINI, _opener

    sink: dict[str, object] = {}
    fetch_llm_grounding(2, api_key="k", prompt=FAKE.grounding_prompt, opener=_opener(_GEMINI, sink))
    sent = json.loads(sink["target"].data)  # type: ignore[attr-defined]
    assert sent["contents"][0]["parts"][0]["text"] == FAKE.grounding_prompt
