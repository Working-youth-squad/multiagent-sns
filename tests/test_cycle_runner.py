"""사이클 오케스트레이터 — 인메모리 결정론 테스트(네트워크·DB 0).

run_cycle의 오케스트레이션 로직(주제 1건 공유·대상별 격리·품질 배선·이벤트 기록)을
InMemoryCycleStore + ScriptedChatModel + 가짜 계약으로 검증한다.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from sns.quality.gate import QualityReport
from sns.runner.cycle import CycleTarget, run_cycle
from sns.runner.store import InMemoryCycleStore
from sns.tools.contracts import ContentFormat, MediaAsset
from sns.tools.fakes import FakeReadStats, FakeRenderMedia, FakeResearchTrends

_CARD_SPEC = {"hook": "3초컷", "title": "walrus", "body": ["a := 10"], "footer": "팔로우"}


class ScriptedChatModel(GenericFakeChatModel):
    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self


def _tool(name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "c1"}])


def _topic_script(index: int = 0, category: str = "꿀팁") -> list[AIMessage]:
    return [
        _tool("choose_topic", {"index": index, "category": category, "summary": "요약"}),
        AIMessage(content="주제 확정"),
    ]


def _content_script(
    spec: dict[str, object] = _CARD_SPEC, *, hook: str = "curiosity", body: str = "본문"
) -> list[AIMessage]:
    return [
        _tool("set_hook", {"pattern": hook}),
        _tool("set_media_spec", {"spec_json": json.dumps(spec, ensure_ascii=False)}),
        AIMessage(content=body),
    ]


def _passing_quality(
    *, media_spec: Mapping[str, object], media: MediaAsset, content_format: ContentFormat
) -> QualityReport:
    return QualityReport(status="passed", checks=())


def _target(mode: str = "auto", fmt: ContentFormat = "feed_image", ch: str = "ch-1") -> CycleTarget:
    return CycleTarget(channel_id=ch, platform="instagram", content_format=fmt, mode=mode)  # type: ignore[arg-type]


def _run(
    script: list[AIMessage], targets: list[CycleTarget], *, assess: Any = _passing_quality
) -> Any:
    store = InMemoryCycleStore()
    result = run_cycle(
        store,
        goal_ref="engagement_depth",
        targets=targets,
        model=ScriptedChatModel(messages=iter(script)),
        research_trends=FakeResearchTrends(),
        read_stats=FakeReadStats(),
        render_media=FakeRenderMedia(),
        assess_quality=assess,
    )
    return store, result


def test_single_target_prepared() -> None:
    store, result = _run(_topic_script() + _content_script(), [_target()])
    assert result.status == "completed"
    assert result.topic_id is not None
    assert len(result.prepared) == 1
    assert len(store.content_items) == 1
    assert len(store.media_assets) == 1
    assert len(store.publications) == 1
    (asset,) = store.media_assets.values()
    assert asset["quality_status"] == "passed"
    (pub,) = store.publications.values()
    assert pub["status"] == "pending"


def test_event_trail() -> None:
    store, _ = _run(_topic_script() + _content_script(), [_target()])
    kinds = [e["kind"] for e in store.events]
    assert kinds == [
        "cycle_started",
        "agent_called",  # topic
        "agent_called",  # content
        "tool_called",  # render_media
        "cycle_completed",
    ]


def test_two_targets_share_one_topic() -> None:
    script = _topic_script() + _content_script() + _content_script(body="본문2")
    store, result = _run(script, [_target(ch="ch-1"), _target(ch="ch-2")])
    assert len(result.prepared) == 2
    assert len(store.topics) == 1  # 주제는 사이클당 1건 (통제변수)
    assert {ci["topic_id"] for ci in store.content_items.values()} == set(store.topics)


def test_no_assessor_defaults_needs_review() -> None:
    store, result = _run(_topic_script() + _content_script(), [_target()], assess=None)
    (asset,) = store.media_assets.values()
    assert asset["quality_status"] == "needs_review"
    assert asset["quality_report"] is None


def test_hybrid_content_needs_review() -> None:
    store, _ = _run(_topic_script() + _content_script(), [_target(mode="hybrid")])
    (ci,) = store.content_items.values()
    assert ci["status"] == "needs_review"


def test_auto_content_approved() -> None:
    store, _ = _run(_topic_script() + _content_script(), [_target(mode="auto")])
    (ci,) = store.content_items.values()
    assert ci["status"] == "approved"


def test_topic_failure_fails_cycle() -> None:
    failing = FakeResearchTrends(
        failing_sources=(
            "google_trends",
            "naver_search",
            "naver_datalab",
            "youtube_popular",
            "github_trending",
        )
    )
    store = InMemoryCycleStore()
    result = run_cycle(
        store,
        goal_ref="engagement_depth",
        targets=[_target()],
        model=ScriptedChatModel(messages=iter(_topic_script())),
        research_trends=failing,
        read_stats=FakeReadStats(),
        render_media=FakeRenderMedia(),
    )
    assert result.status == "failed"
    assert result.topic_id is None
    assert not store.content_items
    assert any(e["kind"] == "error" for e in store.events)


def test_target_failure_isolated() -> None:
    # 대상1 콘텐츠 실패(훅 미설정) → 격리, 대상2는 정상 제작, 사이클은 완료.
    bad_content = [
        _tool("set_media_spec", {"spec_json": json.dumps(_CARD_SPEC, ensure_ascii=False)}),
        AIMessage(content="본문"),
    ]  # set_hook 없음 → ContentRejected
    script = _topic_script() + bad_content + _content_script(body="본문2")
    store, result = _run(script, [_target(ch="ch-1"), _target(ch="ch-2")])
    assert result.status == "completed"
    outcomes = {t.channel_id: t.outcome for t in result.targets}
    assert outcomes == {"ch-1": "failed", "ch-2": "prepared"}
    assert len(store.publications) == 1  # 실패 대상은 원장 없음
    assert any(e["kind"] == "error" for e in store.events)


def test_empty_targets_raises() -> None:
    with pytest.raises(ValueError):
        _run(_topic_script(), [])


def test_deterministic_replay() -> None:
    a = _run(_topic_script() + _content_script(), [_target()])[1]
    b = _run(_topic_script() + _content_script(), [_target()])[1]
    assert a == b
