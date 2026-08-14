"""실패 원인 1줄 — ScriptedChatModel(네트워크 0) + LLM 장애 폴백."""

from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage

from sns.notify.alerts import publish_failure, publish_success
from sns.notify.cause import analyze_cause
from sns.tools.contracts import ToolError

_FAIL = publish_failure("instagram", ToolError("spam_block", "action blocked"), publication_id="p")


def _model(body: str) -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter([AIMessage(content=body)]))


def test_returns_first_nonempty_line() -> None:
    out = analyze_cause(_model("스팸 차단으로 보인다.\n둘째 줄은 버린다"), _FAIL)
    assert out == "스팸 차단으로 보인다."


def test_blank_response_is_none() -> None:
    assert analyze_cause(_model("   \n  "), _FAIL) is None


class _RaisingModel(GenericFakeChatModel):
    def invoke(self, *args: Any, **kwargs: Any) -> BaseMessage:
        raise RuntimeError("LLM down")


def test_llm_failure_swallowed_to_none() -> None:
    # FR-W4: LLM 호출 실패 시 None → 상위가 분류명으로 폴백.
    assert analyze_cause(_RaisingModel(messages=iter([])), _FAIL) is None


def test_no_error_material_returns_none() -> None:
    ok = publish_success("youtube", post_id="v", publication_id="p")
    assert analyze_cause(_model("무언가"), ok) is None
