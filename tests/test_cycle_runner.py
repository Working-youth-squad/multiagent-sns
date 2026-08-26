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
from sns.topic_policy import DEV_MAJOR

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
    spec: dict[str, object] = _CARD_SPEC,
    *,
    hook: str = "curiosity",
    body: str = "본문",
    method: str | None = None,
) -> list[AIMessage]:
    # 영상 포맷은 set_plan이 먼저다 — 코드가 순서를 강제한다([sns.agents.content]).
    plan = [_tool("set_plan", {"video_method": method})] if method else []
    return [
        *plan,
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
        topic_major=DEV_MAJOR,
        targets=targets,
        model=ScriptedChatModel(messages=iter(script)),
        research_trends=FakeResearchTrends(),
        read_stats=FakeReadStats(),
        render_media=FakeRenderMedia(),
        assess_quality=assess,
    )
    return store, result


def test_channel_brief_and_categories_thread_to_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    """온보딩 프로필 주입점 — run_cycle이 channel_brief·topic_categories를 run_topic에 전달."""
    import sns.runner.cycle as cycle_mod

    captured: dict[str, Any] = {}
    real_run_topic = cycle_mod.run_topic

    def spy(model: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return real_run_topic(model, **kwargs)

    monkeypatch.setattr(cycle_mod, "run_topic", spy)
    store = InMemoryCycleStore()
    result = run_cycle(
        store,
        goal_ref="reach_growth",
        topic_major=DEV_MAJOR,
        targets=[_target()],
        model=ScriptedChatModel(
            messages=iter(_topic_script(category="레시피") + _content_script())
        ),
        research_trends=FakeResearchTrends(),
        read_stats=FakeReadStats(),
        render_media=FakeRenderMedia(),
        assess_quality=_passing_quality,
        channel_brief="이 채널의 주제 범위: 요리",
        topic_categories=("레시피", "꿀팁"),
    )
    assert result.status == "completed"
    assert captured["guidance"] == "이 채널의 주제 범위: 요리"
    assert captured["categories"] == ("레시피", "꿀팁")


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
        topic_major=DEV_MAJOR,
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
            topic_major=DEV_MAJOR,
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
        topic_major=DEV_MAJOR,
        targets=[_target(fmt="shorts")],
        model=ScriptedChatModel(
            messages=iter(_topic_script() + _content_script(_VIDEO_SPEC, method="template"))
        ),
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
        topic_major=DEV_MAJOR,
        targets=[_target()],
        model=ScriptedChatModel(messages=iter(_topic_script() + _content_script())),
        research_trends=FakeResearchTrends(),
        read_stats=FakeReadStats(),
        render_media=FakeRenderMedia(),
        assess_quality=_passing_quality,
    )
    used = [t["title"] for t in store.topics.values()]
    assert used == ["google_trends-topic-1", "google_trends-topic-2"], used


# ── FR-Q7 안전 검열 배선 ──────────────────────────────────────────


def _spec_with(topic: str = "정상 주제") -> dict[str, object]:
    return {"topic": topic, "slides": [{"subtitle": "부제", "narration": "한 문장."}]}


def _run_shorts(spec: dict[str, object], *, body: str = "본문", mode: str = "auto") -> Any:
    store = InMemoryCycleStore()
    run_cycle(
        store,
        goal_ref="engagement_depth",
        topic_major=DEV_MAJOR,
        targets=[_target(mode=mode, fmt="shorts")],
        model=ScriptedChatModel(
            messages=iter(_topic_script() + _content_script(spec, body=body, method="template"))
        ),
        research_trends=FakeResearchTrends(),
        read_stats=FakeReadStats(),
        render_media=FakeRenderMedia(),
        assess_quality=_passing_quality,
    )
    return store


def test_clean_content_still_auto_approves() -> None:
    (item,) = _run_shorts(_spec_with()).content_items.values()
    assert item["status"] == "approved"


def test_blocked_material_forces_human_review() -> None:
    """auto 채널이어도 금지 소재가 있으면 자동 승인하지 않는다(FR-Q7: 발행 차단)."""
    (item,) = _run_shorts(_spec_with("대통령 연설 분석")).content_items.values()
    assert item["status"] == "needs_review"


def test_blocked_content_is_never_rendered() -> None:
    """관문이 렌더 앞에 있다 — 막힐 콘텐츠에 TTS·이미지 비용을 쓰지 않는다."""
    store = _run_shorts(_spec_with("대통령 연설 분석"))
    assert store.media_assets == {}
    assert store.publications == {}


def test_blocked_material_in_the_caption_too() -> None:
    """자막만 보면 뚫린다 — 캡션도 그대로 발행된다."""
    store = _run_shorts(_spec_with(), body="크랙 받는 법 알려드립니다")
    (item,) = store.content_items.values()
    assert item["status"] == "needs_review"


def test_findings_are_logged_with_location() -> None:
    """왜 막혔는지 남지 않으면 사람이 승인 화면에서 판단할 수 없다."""
    store = _run_shorts(_spec_with("대통령 연설 분석"))
    notices = [e for e in store.events if e["kind"] == "notice"]
    blob = json.dumps(notices, ensure_ascii=False)
    assert "publish" in blob and "political" in blob and "topic" in blob


# ── FR-A2 근접중복 배선 ───────────────────────────────────────────


def test_near_duplicate_of_recent_content_is_blocked() -> None:
    """어제 낸 대본을 살짝 바꿔 다시 낸 것 — 실제로 일어난 사고다."""
    store = InMemoryCycleStore()
    yesterday = _spec_with()
    store.save_content_item(
        cycle_id="c0", topic_id="t0", content_format="shorts",
        body="본문", media_spec=yesterday, hook_pattern="curiosity", status="approved",
    )  # fmt: skip
    run_cycle(
        store,
        goal_ref="engagement_depth",
        topic_major=DEV_MAJOR,
        targets=[_target(fmt="shorts")],
        model=ScriptedChatModel(
            messages=iter(_topic_script() + _content_script(_spec_with(), method="template"))
        ),
        research_trends=FakeResearchTrends(),
        read_stats=FakeReadStats(),
        render_media=FakeRenderMedia(),
        assess_quality=_passing_quality,
    )
    blob = json.dumps(store.events, ensure_ascii=False)
    assert "근접중복" in blob
    assert store.media_assets == {}, "중복인데 렌더까지 했다"


def test_different_content_passes_the_similarity_gate() -> None:
    store = InMemoryCycleStore()
    store.save_content_item(
        cycle_id="c0", topic_id="t0", content_format="shorts",
        body="본문",
        media_spec={"topic": "ORM 쿼리 폭발", "slides": [
            {"subtitle": "함정", "narration": "반복문마다 쿼리가 나갑니다."}]},
        hook_pattern="curiosity", status="approved",
    )  # fmt: skip
    run_cycle(
        store,
        goal_ref="engagement_depth",
        topic_major=DEV_MAJOR,
        targets=[_target(fmt="shorts")],
        model=ScriptedChatModel(
            messages=iter(_topic_script() + _content_script(_spec_with(), method="template"))
        ),
        research_trends=FakeResearchTrends(),
        read_stats=FakeReadStats(),
        render_media=FakeRenderMedia(),
        assess_quality=_passing_quality,
    )
    assert len(store.media_assets) == 1
