"""Analyst 에이전트 — 결정론·착지점·검증기 결합. 네트워크 0 (ScriptedChatModel)."""

from collections.abc import Callable, Sequence
from typing import Any

import pytest
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from sns.agents.analyst import AnalysisRejected, run_analysis
from sns.tools.fakes import FakePollMetrics, FakeReadStats, FakeWritePlaybook


class ScriptedChatModel(GenericFakeChatModel):
    """T0-4 스파이크 패턴 — bind_tools no-op, 대본이 결정."""

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self


# 게시물 1건·기준선 0건 → 판정 불가가 정답인 상황의 정직한 대본.
_HONEST_BODY = (
    "기준선 표본이 0건이라 판정 불가입니다. 동일 품질도 조회수가 10배 차이 날 수 있습니다."
)
# 지어낸 수치가 든 부정직한 대본.
_DISHONEST_BODY = "공유율이 37.5%로 급등했습니다. 조회수는 10배 차이 날 수 있습니다."


def _script(body: str) -> list[AIMessage]:
    return [
        AIMessage(
            content="",
            tool_calls=[{"name": "read_scoreboard", "args": {}, "id": "call_1"}],
        ),
        AIMessage(content=body),
    ]


def _run(script: list[AIMessage], *, post_ids: tuple[str, ...] = ("XoB6SuTMEvQ",)) -> Any:
    return run_analysis(
        ScriptedChatModel(messages=iter(script)),  # 호출마다 새 모델 (iterator 소진)
        platform="youtube",
        post_ids=post_ids,
        window_index=0,
        poll_metrics=FakePollMetrics(),
        read_stats=FakeReadStats(),
        write_playbook=FakeWritePlaybook(),
    )


def test_honest_analysis_passes() -> None:
    result = _run(_script(_HONEST_BODY))
    assert result.body == _HONEST_BODY
    assert result.insufficient_evidence  # 기준선 0건 — 코드가 결정
    assert not result.playbook_written


def test_deterministic_replay() -> None:
    a = _run(_script(_HONEST_BODY))
    b = _run(_script(_HONEST_BODY))
    assert a == b


def test_fabricated_number_rejected() -> None:
    with pytest.raises(AnalysisRejected) as exc:
        _run(_script(_DISHONEST_BODY))
    assert any("37.5" in r for r in exc.value.reasons)


def test_missing_variance_warning_rejected() -> None:
    with pytest.raises(AnalysisRejected):
        _run([_script(_HONEST_BODY)[0], AIMessage(content="판정 불가입니다.")])


def test_playbook_written_flag() -> None:
    playbook = FakeWritePlaybook()
    script = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_playbook_tool",
                    "args": {
                        "scope": "platform",
                        "guidance": "훅을 질문형으로",
                        "scope_ref": "youtube",
                    },
                    "id": "call_1",
                }
            ],
        ),
        AIMessage(content=_HONEST_BODY),
    ]
    result = run_analysis(
        ScriptedChatModel(messages=iter(script)),
        platform="youtube",
        post_ids=("XoB6SuTMEvQ",),
        window_index=0,
        poll_metrics=FakePollMetrics(),
        read_stats=FakeReadStats(),
        write_playbook=playbook,
    )
    assert result.playbook_written
    assert playbook.entries[("platform", "youtube")] == ["훅을 질문형으로"]


def test_drilldown_tool_available() -> None:
    script = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "poll_window", "args": {"post_id": "XoB6SuTMEvQ", "window": 1}, "id": "c1"}
            ],
        ),
        AIMessage(content=_HONEST_BODY),
    ]
    result = _run(script)
    assert result.body == _HONEST_BODY  # 드릴다운 툴콜이 에러 없이 소화됨


def test_empty_post_ids_raises() -> None:
    with pytest.raises(ValueError):
        _run(_script(_HONEST_BODY), post_ids=())
