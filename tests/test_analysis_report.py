"""분석글·플레이북 착지 (FR-L4·L5) — 표본 선정·검증기 거부·착지. 네트워크 0.

겨누는 것은 넷이다:

1. **표본은 저장소에서 온다** — 분석이 API를 다시 때리지 않는다. 글에 인용된 수치가
   저장된 관측에서만 나올 수 있는 값이어야 검증기를 통과한다는 점을 지렛대로 삼는다.
2. **거부는 아무것도 남기지 않는다** — `analysis_note` 0건 + `playbook` 0건 +
   `run_event` 1건(DoD).
3. **표본이 없으면 조용히 건너뛴다** — 원장에 같은 줄을 매 실행 쌓지 않는다.
4. **한 플랫폼의 사고가 다른 플랫폼을 막지 않는다.**

`ScriptedChatModel`은 Analyst 테스트(T0-4 패턴)에서 그대로 가져온다 — 대본 모델이 두
벌이 되면 한쪽만 고쳐지는 날이 온다.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import AIMessage

from sns.learning.observations import StoredMetrics
from sns.learning.report import (
    NoteReport,
    select_sample,
    write_analysis_note,
    write_analysis_notes,
)
from sns.learning.schedule import REWARD_WINDOW_INDEX
from sns.learning.stores import InMemoryMetricStore, PublishedItem
from sns.tools.contracts import MetricValue, Platform
from tests.test_analyst_agent import ScriptedChatModel

T0 = datetime(2026, 8, 20, tzinfo=UTC)

# 저장된 관측에만 있는 값(42초)을 인용한다 — 코드가 실 어댑터를 물었다면 숫자가 달라져
# 검증기가 거부한다. 기준선 부족이라 '판정 불가'와 분산 경고가 함께 있어야 통과한다.
_HONEST_BODY = (
    "평균 시청 시간은 42초였습니다. 기준선 표본이 부족해 판정 불가입니다. "
    "동일 품질의 콘텐츠도 조회수가 10배 차이 날 수 있습니다."
)
# 스코어보드에 없는 수치(37.5)를 지어낸 대본.
_DISHONEST_BODY = "공유율이 37.5%로 급등했습니다. 조회수는 10배 차이 날 수 있습니다."
# 플랫폼 신호가 달라도 통하는 대본 — 42초는 유튜브 신호(avg_view_duration_s)라
# 인스타 스코어보드에는 없다(있는 척하면 검증기가 거부한다. 그게 검증기의 일이다).
_NEUTRAL_BODY = (
    "기준선 표본이 부족해 판정 불가입니다. 동일 품질의 콘텐츠도 조회수가 10배 차이 날 수 있습니다."
)


def _model(*bodies: str, tool_calls: list[dict[str, Any]] | None = None) -> ScriptedChatModel:
    """분석 1회당 본문 1개. `tool_calls`는 첫 본문 앞에 끼워 넣는다."""
    messages: list[AIMessage] = []
    if tool_calls:
        messages.append(AIMessage(content="", tool_calls=tool_calls))
    messages.extend(AIMessage(content=body) for body in bodies)
    return ScriptedChatModel(messages=iter(messages))


def _seed(
    store: InMemoryMetricStore,
    count: int,
    *,
    platform: Platform = "youtube",
    window: int = REWARD_WINDOW_INDEX,
    observed: bool = True,
    prefix: str = "vid",
    duration: float = 42.0,
) -> tuple[str, ...]:
    """발행 원장에 `count`건을 오래된 순으로 넣고, 옵션으로 그 창을 찍어 둔다.

    지표 키는 플랫폼 신호 정의(`SIGNAL_DEFS`)를 따른다 — 아무 키나 넣으면 스코어보드가
    전부 None이 되어 '값을 읽었다'를 증명하지 못한다.
    """
    duration_key = "avg_view_duration_s" if platform == "youtube" else "avg_watch_time_ms"
    post_ids = []
    start = len(store.items)
    for i in range(start, start + count):
        pub_id, post_id = f"pub-{i}", f"{prefix}-{i}"
        store.add_published_item(
            PublishedItem(
                publication_id=pub_id,
                platform=platform,
                external_post_id=post_id,
                published_at=T0 + timedelta(days=i),
                content_format="shorts" if platform == "youtube" else "reels",
                topic_id="topic-1",
                channel_mode="auto",
            )
        )
        if observed:
            store.save_observation(
                publication_id=pub_id,
                window_index=window,
                values=[
                    MetricValue(duration_key, duration, False),
                    MetricValue("views", None, True),
                ],
            )
        post_ids.append(post_id)
    return tuple(post_ids)


# ── 표본 선정 ───────────────────────────────────────────────────────


def test_newest_published_is_the_target_and_the_rest_is_the_baseline() -> None:
    """'마지막 = 최신'은 발행 원장 순서에 기댄 전제다 — 여기서 못박는다."""
    store = InMemoryMetricStore()
    posts = _seed(store, 4)
    sample = select_sample(StoredMetrics(store), "youtube")
    assert sample is not None
    assert sample.target_post_id == posts[-1]
    assert sample.baseline_post_ids == posts[:-1]
    assert sample.post_ids == posts  # run_analysis 계약: 마지막이 대상


def test_unpolled_posts_are_not_in_the_sample() -> None:
    """폴링 전 게시물을 기준선에 넣으면 '값이 없다'가 '성과가 없다'로 둔갑한다."""
    store = InMemoryMetricStore()
    observed = _seed(store, 2)
    _seed(store, 3, observed=False, prefix="unpolled")
    sample = select_sample(StoredMetrics(store), "youtube")
    assert sample is not None
    assert set(sample.post_ids) == set(observed)


def test_baseline_keeps_only_the_most_recent_window() -> None:
    """1년 전 게시물이 중앙값을 끌면 그건 지금 이 계정의 기준선이 아니다."""
    store = InMemoryMetricStore()
    posts = _seed(store, 6)
    sample = select_sample(StoredMetrics(store), "youtube", baseline_limit=2)
    assert sample is not None
    assert sample.baseline_post_ids == posts[-3:-1]  # 대상 바로 앞의 2건


def test_verdict_needs_five_baseline_posts() -> None:
    store = InMemoryMetricStore()
    _seed(store, 5)  # 대상 1 + 기준선 4
    small = select_sample(StoredMetrics(store), "youtube")
    assert small is not None and not small.verdict_available
    _seed(store, 1)
    enough = select_sample(StoredMetrics(store), "youtube")
    assert enough is not None and enough.verdict_available


def test_no_observations_means_no_sample() -> None:
    store = InMemoryMetricStore()
    _seed(store, 2, observed=False)
    assert select_sample(StoredMetrics(store), "youtube") is None


# ── 착지 ────────────────────────────────────────────────────────────


def test_honest_note_lands_with_values_read_from_the_store() -> None:
    """네트워크를 타지 않는 증거: 인용된 42초는 저장된 관측에만 있는 값이다."""
    store = InMemoryMetricStore()
    _seed(store, 3)
    report = write_analysis_note(_model(_HONEST_BODY), store, platform="youtube")

    assert report.note_id is not None
    assert report.insufficient_evidence  # 기준선 2건 — 코드가 결정
    assert report.baseline_count == 2
    assert len(store.notes) == 1
    assert store.notes[0]["body"] == _HONEST_BODY
    assert store.notes[0]["insufficient_evidence"] is True
    assert store.events == []  # 성공 경로는 error를 남기지 않는다


def test_rejected_analysis_saves_nothing_and_logs_once() -> None:
    """DoD — analysis_note 0건 + run_event 1건."""
    store = InMemoryMetricStore()
    _seed(store, 3)
    report = write_analysis_note(_model(_DISHONEST_BODY), store, platform="youtube")

    assert report.note_id is None
    assert any("37.5" in reason for reason in report.rejected_reasons)
    assert store.notes == []
    assert len(store.events) == 1
    event = store.events[0]
    assert event["kind"] == "error"
    payload = event["payload"]
    assert isinstance(payload, dict)
    assert payload["reason"] == "analysis_rejected"
    assert payload["post_id"] == "vid-2"
    assert "37.5" in payload["body_head"]  # type: ignore[operator]


def test_playbook_from_a_rejected_analysis_is_dropped() -> None:
    """거부된 글의 지침이 살아남으면 그게 다음 사이클 콘텐츠의 근거가 된다."""
    store = InMemoryMetricStore()
    _seed(store, 3)
    write_playbook_call = [
        {
            "name": "write_playbook_tool",
            "args": {"scope": "platform", "guidance": "훅을 질문형으로", "scope_ref": "youtube"},
            "id": "call_1",
        }
    ]
    report = write_analysis_note(
        _model(_DISHONEST_BODY, tool_calls=write_playbook_call), store, platform="youtube"
    )

    assert report.rejected_reasons  # 거부됨
    assert store.playbooks == []  # 지침도 함께 버려졌다
    assert not report.playbook_written
    payload = store.events[0]["payload"]
    assert isinstance(payload, dict)
    assert payload["dropped_playbook_entries"] == 1  # 버려진 사실은 남는다


def test_playbook_lands_only_after_the_validator_passes() -> None:
    store = InMemoryMetricStore()
    _seed(store, 3)
    write_playbook_call = [
        {
            "name": "write_playbook_tool",
            "args": {"scope": "platform", "guidance": "훅을 질문형으로", "scope_ref": "youtube"},
            "id": "call_1",
        }
    ]
    report = write_analysis_note(
        _model(_HONEST_BODY, tool_calls=write_playbook_call), store, platform="youtube"
    )

    assert report.playbook_written
    assert [(p.scope, p.scope_ref, p.version, p.guidance) for p in store.playbooks] == [
        ("platform", "youtube", 1, "훅을 질문형으로")
    ]


def test_empty_sample_skips_without_touching_the_ledger() -> None:
    """매 실행이 같은 사실을 append-only 원장에 쌓지 않는다(폴러의 '놓친 창'과 같은 규율)."""
    store = InMemoryMetricStore()
    report = write_analysis_note(_model(_HONEST_BODY), store, platform="youtube")

    assert report.skipped is not None
    assert report.note_id is None
    assert store.notes == []
    assert store.events == []


def test_model_failure_is_reported_not_raised() -> None:
    store = InMemoryMetricStore()
    _seed(store, 3)
    report = write_analysis_note(_model(), store, platform="youtube")  # 대본 고갈 → 예외

    assert report.note_id is None
    assert report.error is not None
    assert store.notes == []
    payload = store.events[0]["payload"]
    assert isinstance(payload, dict)
    assert payload["reason"] == "analysis_failed"


# ── 여러 플랫폼 ─────────────────────────────────────────────────────


def test_one_platform_does_not_block_the_other() -> None:
    """인스타 표본이 없어도 유튜브 분석은 착지한다."""
    store = InMemoryMetricStore()
    _seed(store, 3)
    reports = write_analysis_notes(_model(_NEUTRAL_BODY), store, platforms=("instagram", "youtube"))

    assert [r.platform for r in reports] == ["instagram", "youtube"]
    assert reports[0].skipped is not None
    assert reports[1].note_id is not None
    assert len(store.notes) == 1


def test_landing_failure_is_isolated_to_its_platform() -> None:
    """적재가 터져도 다음 플랫폼은 분석된다 — 마지막 방어선."""

    class BrokenOnce(InMemoryMetricStore):
        """첫 적재만 터진다 — 두 번째 플랫폼이 정상적으로 착지하는지 보려는 것이다."""

        calls = 0

        def save_analysis_note(self, **kwargs: Any) -> str:
            BrokenOnce.calls += 1
            if BrokenOnce.calls == 1:
                raise RuntimeError("원장 적재 실패")
            return super().save_analysis_note(**kwargs)

    store = BrokenOnce()
    _seed(store, 3, platform="youtube")
    _seed(store, 3, platform="instagram", prefix="ig")
    reports = write_analysis_notes(
        _model(_NEUTRAL_BODY, _NEUTRAL_BODY), store, platforms=("youtube", "instagram")
    )

    assert reports[0].error is not None
    assert reports[1].note_id is not None
    assert len(store.notes) == 1


def test_both_platforms_read_one_ledger_snapshot() -> None:
    """스냅샷이 하나라 두 플랫폼이 같은 표본 시점을 본다(결정론)."""
    store = InMemoryMetricStore()
    _seed(store, 3, platform="youtube")
    _seed(store, 3, platform="instagram", prefix="ig")
    reports = write_analysis_notes(
        _model(_NEUTRAL_BODY, _NEUTRAL_BODY), store, platforms=("youtube", "instagram")
    )

    assert all(r.note_id for r in reports)
    assert [r.target_post_id for r in reports] == ["vid-2", "ig-5"]


def test_summary_reads_as_one_line() -> None:
    skipped = NoteReport(platform="youtube", window_index=2, skipped="표본 없음")
    assert "건너뜀" in skipped.summary()
