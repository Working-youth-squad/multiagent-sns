"""Topic 에이전트 — 근거 선택·카테고리 검증·결정론. 네트워크 0 (ScriptedChatModel)."""

from collections.abc import Callable, Sequence
from typing import Any

import pytest
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from sns.agents.topic import TopicSelectionError, _match_recent, run_topic
from sns.tools.fakes import DEFAULT_SOURCES, FakeReadStats, FakeResearchTrends


class ScriptedChatModel(GenericFakeChatModel):
    """bind_tools no-op — 대본(messages)이 결정한다 (T0-4 패턴)."""

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self


def _choose(index: int, category: str, summary: str = "한 줄 요약") -> list[AIMessage]:
    return [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "choose_topic",
                    "args": {"index": index, "category": category, "summary": summary},
                    "id": "c1",
                }
            ],
        ),
        AIMessage(content="주제 확정."),
    ]


def _run(script: list[AIMessage], *, research: FakeResearchTrends | None = None) -> Any:
    return run_topic(
        ScriptedChatModel(messages=iter(script)),
        platform="youtube",
        research_trends=research or FakeResearchTrends(),
        read_stats=FakeReadStats(),
    )


def test_selects_grounded_candidate() -> None:
    result = _run(_choose(0, "꿀팁"))
    # 후보 0 = 첫 성공 소스의 첫 아이템 (FakeResearchTrends 결정론).
    assert result.title == "google_trends-topic-1"
    assert result.source == "google_trends"
    assert result.category == "꿀팁"
    assert result.summary == "한 줄 요약"


def test_deterministic_replay() -> None:
    assert _run(_choose(1, "신기술")) == _run(_choose(1, "신기술"))


def test_invalid_category_not_confirmed() -> None:
    # 잘못된 카테고리 → choose_topic이 오류를 돌려주고 확정 안 됨 → 최종 도달 시 실패.
    with pytest.raises(TopicSelectionError):
        _run(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "choose_topic",
                            "args": {"index": 0, "category": "정치", "summary": "x"},
                            "id": "c1",
                        }
                    ],
                ),
                AIMessage(content="끝"),
            ]
        )


def test_out_of_range_index_not_confirmed() -> None:
    with pytest.raises(TopicSelectionError):
        _run(_choose(999, "꿀팁"))


def test_no_choose_call_raises() -> None:
    with pytest.raises(TopicSelectionError):
        _run([AIMessage(content="아무것도 안 고름")])


def test_all_sources_failed_raises() -> None:
    # 모든 소스 실패 = 후보 0건 → 지어내지 않고 즉시 실패 (FR-G4).
    failing = FakeResearchTrends(
        failing_sources=(
            "google_trends",
            "naver_search",
            "naver_datalab",
            "youtube_popular",
            "github_trending",
        )
    )
    with pytest.raises(TopicSelectionError):
        _run(_choose(0, "꿀팁"), research=failing)


def test_read_tools_do_not_error() -> None:
    # read_trends·read_topic_stats 툴콜이 에러 없이 소화되고 이후 확정된다.
    script = [
        AIMessage(content="", tool_calls=[{"name": "read_trends", "args": {}, "id": "c1"}]),
        AIMessage(content="", tool_calls=[{"name": "read_topic_stats", "args": {}, "id": "c2"}]),
        *_choose(2, "개발자유머"),
    ]
    result = _run(script)
    assert result.category == "개발자유머"


# ── 발행 이력 중복 차단 ───────────────────────────────────────────
#
# 실제로 어제와 오늘 같은 영상이 나갔다. GitHub 트렌딩은 같은 저장소를 며칠씩 노출하는데
# 주제 선정이 과거 발행 이력을 안 봤고, temperature=0이라 같은 입력 → 같은 대본이었다.
# 프롬프트로 "겹치지 마라" 부탁하는 대신 **후보 목록에서 코드가 빼버린다**(통제=코드).


def _run_excluding(script: list[AIMessage], exclude: Sequence[str]) -> Any:
    return run_topic(
        ScriptedChatModel(messages=iter(script)),
        platform="youtube",
        research_trends=FakeResearchTrends(),
        read_stats=FakeReadStats(),
        exclude_titles=exclude,
    )


def test_recent_topic_is_removed_from_candidates() -> None:
    """제외된 주제가 빠지면 그 뒤 후보들의 index가 앞으로 당겨진다."""
    result = _run_excluding(_choose(0, "꿀팁"), ["google_trends-topic-1"])
    assert result.title == "google_trends-topic-2"


def test_near_duplicate_is_also_removed() -> None:
    """어제 'cursor/plugins'를 올렸으면 오늘 'Cursor plugins'도 같은 주제다."""
    assert _match_recent("cursor/plugins", ("Cursor plugins",))
    assert _match_recent("Cursor Plugins 모음", ("cursor/plugins",))


def test_unrelated_topic_survives() -> None:
    """조금 겹친다고 다 막으면 후보가 말라붙는다."""
    assert not _match_recent("cursor/plugins", ("vercel/next.js",))
    assert not _match_recent("파이썬 리스트 성능", ("파이썬 데코레이터 입문",))


def test_agent_never_sees_excluded_candidates() -> None:
    """프롬프트 부탁이 아니라 목록에서 사라져야 한다 — 보이면 언젠가 고른다."""
    seen: list[str] = []

    class Recording(ScriptedChatModel):
        def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:
            for tool in tools:
                if getattr(tool, "name", "") == "read_trends":
                    seen.append(tool.invoke({}))
            return self

    run_topic(
        Recording(messages=iter(_choose(0, "꿀팁"))),
        platform="youtube",
        research_trends=FakeResearchTrends(),
        read_stats=FakeReadStats(),
        exclude_titles=["google_trends-topic-1"],
    )
    assert seen and "google_trends-topic-1" not in seen[0]


def test_all_candidates_excluded_fails_loudly() -> None:
    """새 주제가 없으면 같은 걸 또 내보내느니 사이클을 실패시킨다."""
    everything = [f"{s}-topic-{i}" for s in DEFAULT_SOURCES for i in range(1, 4)]
    with pytest.raises(TopicSelectionError, match="최근 발행"):
        _run_excluding(_choose(0, "꿀팁"), everything)


def test_no_exclusions_behaves_as_before() -> None:
    assert _run_excluding(_choose(0, "꿀팁"), []).title == "google_trends-topic-1"


# ── 온보딩 채널 프로필 주입 (categories·guidance) ─────────────────


def test_custom_categories_replace_default() -> None:
    result = run_topic(
        ScriptedChatModel(messages=iter(_choose(0, "레시피"))),
        platform="youtube",
        research_trends=FakeResearchTrends(),
        read_stats=FakeReadStats(),
        categories=("레시피", "꿀팁"),
    )
    assert result.category == "레시피"


def test_default_category_invalid_under_custom_set() -> None:
    # 카테고리를 교체하면 기본 5종("신기술")은 더 이상 유효하지 않다.
    with pytest.raises(TopicSelectionError):
        run_topic(
            ScriptedChatModel(messages=iter(_choose(0, "신기술"))),
            platform="youtube",
            research_trends=FakeResearchTrends(),
            read_stats=FakeReadStats(),
            categories=("레시피",),
        )


def test_guidance_reaches_the_agent_request() -> None:
    seen: list[str] = []

    class Recording(ScriptedChatModel):
        def _generate(self, messages: Any, **kwargs: Any) -> Any:
            seen.append("\n".join(str(m.content) for m in messages))
            return super()._generate(messages, **kwargs)

    run_topic(
        Recording(messages=iter(_choose(0, "꿀팁"))),
        platform="youtube",
        research_trends=FakeResearchTrends(),
        read_stats=FakeReadStats(),
        guidance="이 채널의 주제 범위: 요리 (세부: 비건)",
    )
    assert any("주제 범위: 요리" in s for s in seen)
