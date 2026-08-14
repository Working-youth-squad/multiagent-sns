"""알림 디스패치 — 원인부착·전송·적재 오케스트레이션 + 장애 격리(인메모리)."""

from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage

from sns.notify.alerts import event_kind, publish_failure, publish_success
from sns.notify.dispatch import InMemoryAlertSink, dispatch_alert
from sns.tools.contracts import ToolError

_FAIL = publish_failure("instagram", ToolError("auth", "token expired"), publication_id="pub-1")
_OK = publish_success("youtube", post_id="vid-1", publication_id="pub-2")


def _model(body: str) -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter([AIMessage(content=body)]))


def test_failure_records_error_event_and_delivers() -> None:
    sink = InMemoryAlertSink()
    sent: list[dict[str, object]] = []
    result = dispatch_alert(
        _FAIL, sink=sink, sender=sent.append, model=_model("토큰 만료로 보인다.")
    )

    assert result.recorded and result.delivered and result.cause_analyzed
    assert len(sink.alerts) == 1
    recorded = sink.alerts[0]
    assert event_kind(recorded) == "error"
    assert recorded.cause_line == "토큰 만료로 보인다."
    # 전송 페이로드에 원인·분류가 실렸다.
    desc = sent[0]["embeds"][0]["description"]  # type: ignore[index]
    assert "원인: 토큰 만료로 보인다." in desc
    assert "분류: auth" in desc


def test_success_records_notice() -> None:
    sink = InMemoryAlertSink()
    result = dispatch_alert(_OK, sink=sink)
    assert result.recorded and not result.delivered  # sender 없음
    assert event_kind(sink.alerts[0]) == "notice"


def test_llm_absent_still_records_and_delivers_with_class() -> None:
    # 모델 미주입 → cause_line 없이도 분류명이 실린다(FR-W4 폴백).
    sink = InMemoryAlertSink()
    sent: list[dict[str, object]] = []
    result = dispatch_alert(_FAIL, sink=sink, sender=sent.append)
    assert result.recorded and result.delivered and not result.cause_analyzed
    assert sink.alerts[0].cause_line is None
    assert "분류: auth" in sent[0]["embeds"][0]["description"]  # type: ignore[index]


class _RaisingModel(GenericFakeChatModel):
    def invoke(self, *args: Any, **kwargs: Any) -> BaseMessage:
        raise RuntimeError("LLM down")


def test_llm_failure_does_not_block_dispatch() -> None:
    sink = InMemoryAlertSink()
    sent: list[dict[str, object]] = []
    result = dispatch_alert(
        _FAIL, sink=sink, sender=sent.append, model=_RaisingModel(messages=iter([]))
    )
    assert result.recorded and result.delivered and not result.cause_analyzed
    assert len(sent) == 1  # 전송은 됐다


def test_webhook_failure_swallowed_but_db_recorded() -> None:
    # 전송 실패해도 DB 적재는 남는다(불변식).
    def boom(_: dict[str, object]) -> None:
        raise RuntimeError("discord down")

    sink = InMemoryAlertSink()
    result = dispatch_alert(_FAIL, sink=sink, sender=boom)
    assert result.recorded and not result.delivered
    assert len(sink.alerts) == 1


class _RaisingSink:
    def record(self, alert: Any) -> None:
        raise RuntimeError("db down")


def test_db_failure_swallowed_never_raises() -> None:
    # dispatch는 실패 처리 경로에서 불린다 — 절대 던지지 않는다.
    sent: list[dict[str, object]] = []
    result = dispatch_alert(_FAIL, sink=_RaisingSink(), sender=sent.append)
    assert not result.recorded and result.delivered  # 적재 실패해도 전송은 시도
