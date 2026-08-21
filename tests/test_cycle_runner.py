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


def test_manual_target_assigned_without_content() -> None:
    # manual(수동) 대상: AI 초안·발행 대기를 만들지 않고 주제 배정 notice만 남긴다.
    store, result = _run(_topic_script(), [_target(mode="manual")])
    assert result.status == "completed"  # 주제 전달이 manual 대상의 이번 사이클 몫 전부
    (t,) = result.targets
    assert t.outcome == "manual_assigned"
    assert t.content_item_id is None and t.publication_id is None
    assert not store.content_items and not store.publications
    (notice,) = [e for e in store.events if e["kind"] == "notice"]
    assert notice["payload"]["reason"] == "manual_assignment"
    assert notice["payload"]["topic_id"] in store.topics


def test_manual_and_auto_share_same_topic() -> None:
    # 3모드 비교의 전제: manual도 같은 사이클의 같은 주제(프롬프트)를 배정받는다.
    script = _topic_script() + _content_script()
    store, result = _run(
        script, [_target(mode="manual", ch="ch-m"), _target(mode="auto", ch="ch-a")]
    )
    outcomes = {t.channel_id: t.outcome for t in result.targets}
    assert outcomes == {"ch-m": "manual_assigned", "ch-a": "prepared"}
    assert len(store.topics) == 1  # 주제는 사이클당 1건 — manual/auto 공유
    (pub,) = store.publications.values()  # 기계 발행 대기는 auto 것 하나뿐
    assert pub["mode"] == "auto"  # 발행 시점 모드 스냅샷(증빙)


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


def test_all_targets_failed_marks_cycle_failed() -> None:
    # 전 대상 콘텐츠 실패(훅 미설정) → prepared=0 → 사이클 자체가 failed(오독 방지).
    bad = [
        _tool("set_media_spec", {"spec_json": json.dumps(_CARD_SPEC, ensure_ascii=False)}),
        AIMessage(content="본문"),
    ]
    script = _topic_script() + bad + bad
    store, result = _run(script, [_target(ch="ch-1"), _target(ch="ch-2")])
    assert result.status == "failed"
    assert result.prepared == ()
    (cycle,) = store.cycles.values()
    assert cycle["status"] == "failed"


class _BoomError(Exception):
    """인프라/영속화 실패 모사."""


class _FailingStore(InMemoryCycleStore):
    def save_media_asset(self, **kwargs: Any) -> str:
        raise _BoomError("DB 저장 실패")


def test_infra_failure_marks_failed_and_propagates() -> None:
    # 도메인 아닌 예외(영속화 실패)는 격리하지 않는다 — 사이클을 failed로 표기 후 전파.
    store = _FailingStore()
    with pytest.raises(_BoomError):
        run_cycle(
            store,
            goal_ref="engagement_depth",
            targets=[_target()],
            model=ScriptedChatModel(messages=iter(_topic_script() + _content_script())),
            research_trends=FakeResearchTrends(),
            read_stats=FakeReadStats(),
            render_media=FakeRenderMedia(),
            assess_quality=_passing_quality,
        )
    # running으로 방치되지 않고 failed로 종결됐는가.
    (cycle,) = store.cycles.values()
    assert cycle["status"] == "failed"
    assert any(e["kind"] == "error" for e in store.events)


def test_empty_targets_raises() -> None:
    with pytest.raises(ValueError):
        _run(_topic_script(), [])


def test_deterministic_replay() -> None:
    a = _run(_topic_script() + _content_script(), [_target()])[1]
    b = _run(_topic_script() + _content_script(), [_target()])[1]
    assert a == b


# ── 주제 이미지 해소 seam ─────────────────────────────────────────


_VIDEO_SPEC: dict[str, object] = {
    "topic": "주제 한 줄",
    "slides": [{"subtitle": "부제", "narration": "한 문장.", "image_query": "server room"}],
}


def _resolving(spec: Mapping[str, object]) -> Any:
    """image_query를 해소한 척하는 가짜 — 출처·촬영자를 spec에 남긴다."""
    from sns.render.images.resolve import ImageResolution

    slides = [
        {
            **s,
            "image_ref": "mem://image/abc.png",
            "image_source": "https://www.pexels.com/photo/42/",
            "image_credit": "Christina Morillo",
        }
        for s in spec["slides"]  # type: ignore[union-attr]
    ]
    return ImageResolution({**spec, "slides": slides}, ("slides[9]: 후보 없음",))


def _run_video(resolve: Any) -> Any:
    store = InMemoryCycleStore()
    run_cycle(
        store,
        goal_ref="engagement_depth",
        targets=[_target(fmt="shorts")],
        model=ScriptedChatModel(messages=iter(_topic_script() + _content_script(_VIDEO_SPEC))),
        research_trends=FakeResearchTrends(),
        read_stats=FakeReadStats(),
        render_media=FakeRenderMedia(),
        assess_quality=_passing_quality,
        resolve_media_spec=resolve,
    )
    return store


def test_resolved_spec_is_what_gets_saved() -> None:
    """해소 전 spec을 저장하면 렌더가 사진 없이 돌고 원장도 사실과 어긋난다."""
    store = _run_video(_resolving)
    (item,) = store.content_items.values()
    slides = item["media_spec"]["slides"]
    assert slides[0]["image_ref"] == "mem://image/abc.png"


def test_credit_line_lands_in_the_body() -> None:
    """Pexels API 가이드라인이 요구하는 출처 표기 — 캡션에 붙어 원장에 남아야 한다."""
    store = _run_video(_resolving)
    (item,) = store.content_items.values()
    assert "Christina Morillo" in item["body"]
    assert "https://www.pexels.com/photo/42/" in item["body"]


def test_resolution_notes_are_logged() -> None:
    """사진이 안 붙은 이유를 조용히 삼키면 나중에 물어볼 수 없다."""
    store = _run_video(_resolving)
    notices = [e for e in store.events if e["kind"] == "notice"]
    assert any("후보 없음" in json.dumps(e["payload"], ensure_ascii=False) for e in notices)


def test_without_resolver_spec_and_body_pass_through() -> None:
    """seam 미배선이 기본값 — 이미지 트랙 없이도 사이클은 그대로 돈다."""
    store = _run_video(None)
    (item,) = store.content_items.values()
    assert "image_ref" not in item["media_spec"]["slides"][0]
    assert "Pexels" not in item["body"]


# ── 주제 중복 차단 배선 ───────────────────────────────────────────


def test_recent_topics_are_excluded_from_the_next_cycle() -> None:
    """어제 쓴 주제가 오늘 후보에서 빠져야 한다 — 실제로 같은 영상이 두 번 나갔다."""
    store = InMemoryCycleStore()
    store.save_topic(title="google_trends-topic-1", summary="어제 쓴 주제", source="google_trends")
    run_cycle(
        store,
        goal_ref="engagement_depth",
        targets=[_target()],
        model=ScriptedChatModel(messages=iter(_topic_script() + _content_script())),
        research_trends=FakeResearchTrends(),
        read_stats=FakeReadStats(),
        render_media=FakeRenderMedia(),
        assess_quality=_passing_quality,
    )
    used = [t["title"] for t in store.topics.values()]
    assert used == ["google_trends-topic-1", "google_trends-topic-2"], used
