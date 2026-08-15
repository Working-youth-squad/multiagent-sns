"""Topic 에이전트 — 근거 선택·카테고리 검증·결정론. 네트워크 0 (ScriptedChatModel)."""

from collections.abc import Callable, Sequence
from typing import Any

import pytest
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from sns.agents.topic import TopicSelectionError, run_topic
from sns.tools.fakes import FakeReadStats, FakeResearchTrends


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
