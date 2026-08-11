"""T0-4 스파이크: deepagents 3대 계약 검증 (주입 · 결정론 · 착지점).

리포트: docs/spikes/t0-4-deepagents-spike.md
API 키 없이(오프라인) 가짜 모델로 전체 사이클이 도는지가 핵심.
"""

from collections.abc import Callable, Sequence
from typing import Any

from deepagents import create_deep_agent
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool, tool

from sns.tools.fakes import FakePublish, FakeRenderMedia


class ScriptedChatModel(GenericFakeChatModel):
    """대본대로만 응답하는 주입용 가짜 모델.

    발견사항: GenericFakeChatModel은 bind_tools 미구현(NotImplementedError) —
    create_agent가 항상 bind_tools를 호출하므로 no-op 오버라이드 필요.
    """

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self  # 툴 스키마는 무시, 대본이 결정


def _scripted_model() -> ScriptedChatModel:
    """publish_tool 호출 → 완료 보고, 2턴 고정 대본."""
    script = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "publish_tool",
                    "args": {"caption": "훅 문장", "idempotency_key": "cycle1-ig"},
                    "id": "call_1",
                }
            ],
        ),
        AIMessage(content="발행 완료: cycle1-ig"),
    ]
    return ScriptedChatModel(messages=iter(script))


def _run_cycle() -> tuple[dict[str, Any], FakePublish]:
    """가짜 모델 + T0-3 가짜 툴로 미니 발행 사이클 1회 실행."""
    fake_publish = FakePublish()
    media = FakeRenderMedia()({"template": "card"}, "image")

    @tool
    def publish_tool(caption: str, idempotency_key: str) -> str:
        """콘텐츠를 발행하고 post_id를 반환한다."""
        result = fake_publish("instagram", media, caption, idempotency_key)
        return result.post_id or "error"

    agent = create_deep_agent(model=_scripted_model(), tools=[publish_tool])
    state: dict[str, Any] = agent.invoke({"messages": [HumanMessage("사이클 실행")]})
    return state, fake_publish


def test_injection_runs_offline() -> None:
    # 계약 1 — 주입: BaseChatModel 인스턴스면 뭐든 주입 가능, API 키 불필요
    state, _ = _run_cycle()
    assert state["messages"][-1].content == "발행 완료: cycle1-ig"


def test_deterministic_replay() -> None:
    # 계약 2 — 결정론: 같은 대본·같은 툴 → 두 번 실행해도 같은 결과 (NFR-2)
    state_a, publish_a = _run_cycle()
    state_b, publish_b = _run_cycle()
    assert state_a["messages"][-1].content == state_b["messages"][-1].content
    assert publish_a.calls == publish_b.calls
    assert publish_a.results == publish_b.results


def test_landing_points_only_via_tools() -> None:
    # 계약 3 — 착지점: LLM 부작용 경로 = 우리가 넘긴 툴뿐.
    state, fake_publish = _run_cycle()
    # 툴 계약을 통해서만 발행이 일어났다 (FR-C2)
    assert fake_publish.calls == ["cycle1-ig"]
    # 내장 파일 툴은 가상(state) 파일시스템 — 이번 사이클에서 실측 부작용 없음
    assert not state.get("files")
