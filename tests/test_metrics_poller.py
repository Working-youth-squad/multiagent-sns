"""지표 폴러 (FR-L1, M6) — 가짜 어댑터 + InMemory 저장소로 DB 없이.

겨누는 것은 "값을 잘 옮기는가"보다 **무엇이 루프를 끊는가**다. 한 건의 어댑터 오류나
아직 없는 IG 폴러가 훑기를 멈추면 실험이 통째로 선다 — 발행 라우터(`sns/publish/router.py`)
를 만든 것과 같은 이유의 자리다.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sns.learning.poller import poll_due_metrics
from sns.learning.stores import InMemoryMetricStore, PublishedItem
from sns.tools.contracts import MetricValue, Platform

PUBLISHED = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)


def _at(hours: float) -> datetime:
    return PUBLISHED + timedelta(hours=hours)


def _item(pub_id: str = "pub-1", *, platform: Platform = "youtube", offset_h: int = 0):
    return PublishedItem(
        publication_id=pub_id,
        platform=platform,
        external_post_id=f"post-{pub_id}",
        published_at=PUBLISHED + timedelta(hours=offset_h),
        content_format="shorts" if platform == "youtube" else "reels",
        topic_id="topic-1",
        channel_mode="auto",
    )


def _store(*items: PublishedItem) -> InMemoryMetricStore:
    store = InMemoryMetricStore()
    for item in items:
        store.add_published_item(item)
    return store


class FakePoller:
    """호출을 기록하는 `PollMetrics`. `fail_for`에 든 post_id에서만 터진다."""

    def __init__(
        self, values: Sequence[MetricValue] = (), *, fail_for: frozenset[str] = frozenset()
    ):
        self.values = tuple(values) or (MetricValue("views", 7.0, False),)
        self.fail_for = fail_for
        self.calls: list[tuple[str, int]] = []

    def __call__(
        self, platform: Platform, post_id: str, window_index: int
    ) -> tuple[MetricValue, ...]:
        self.calls.append((post_id, window_index))
        if post_id in self.fail_for:
            raise RuntimeError("quota exceeded")
        return self.values


# ── 정상 경로 ───────────────────────────────────────────────────────


def test_due_window_is_polled_and_stored() -> None:
    store = _store(_item())
    poller = FakePoller()
    report = poll_due_metrics(store=store, pollers={"youtube": poller}, now=_at(6))
    assert poller.calls == [("post-pub-1", 0)]
    assert report.observed == 1
    assert store.read_observation(publication_id="pub-1", window_index=0) == (
        MetricValue("views", 7.0, False),
    )


def test_poll_records_the_run_even_when_everything_is_missing() -> None:
    """폴러가 안 돈 것과 API가 값을 안 준 것은 다른 사건이다 — 후자는 흔적이 남는다."""
    store = _store(_item())
    poller = FakePoller([MetricValue("views", None, True)])
    poll_due_metrics(store=store, pollers={"youtube": poller}, now=_at(6))
    events = [e for e in store.events if e["kind"] == "metric_polled"]
    assert len(events) == 1
    assert events[0]["payload"] == {
        "publication_id": "pub-1",
        "platform": "youtube",
        "window_index": 0,
        "keys": 1,
        "missing": 1,
    }
    stored = store.read_observation(publication_id="pub-1", window_index=0)
    assert stored[0].value is None and stored[0].missing is True


def test_observed_at_uses_the_injected_clock() -> None:
    store = _store(_item())
    poll_due_metrics(store=store, pollers={"youtube": FakePoller()}, now=_at(6))
    assert store.observations[("pub-1", 0)][0] == _at(6)


def test_already_observed_window_is_not_polled_again() -> None:
    store = _store(_item())
    store.save_observation(
        publication_id="pub-1", window_index=0, values=[MetricValue("views", 1.0, False)]
    )
    poller = FakePoller()
    report = poll_due_metrics(store=store, pollers={"youtube": poller}, now=_at(6))
    assert poller.calls == []  # 쿼터를 태우지 않는다.
    assert report.observed == 0


def test_race_with_another_run_is_absorbed_not_overwritten() -> None:
    """다른 프로세스가 방금 같은 창을 찍은 경우 — 저장소가 흡수하고 값은 먼저 것이 남는다."""

    class HidesObservations(InMemoryMetricStore):
        def published_items(self, *, since=None, limit=200):  # type: ignore[no-untyped-def]
            return tuple(
                PublishedItem(**{**vars(i), "observed_windows": ()})
                for i in super().published_items(since=since, limit=limit)
            )

    store = HidesObservations()
    store.add_published_item(_item())
    store.save_observation(
        publication_id="pub-1", window_index=0, values=[MetricValue("views", 1.0, False)]
    )
    report = poll_due_metrics(store=store, pollers={"youtube": FakePoller()}, now=_at(6))
    assert (report.observed, report.absorbed) == (0, 1)
    assert store.read_observation(publication_id="pub-1", window_index=0) == (
        MetricValue("views", 1.0, False),
    )


# ── 실패가 어디서 멈추는가 ──────────────────────────────────────────


def test_adapter_error_does_not_stop_the_sweep() -> None:
    store = _store(_item("pub-bad"), _item("pub-good"))
    poller = FakePoller(fail_for=frozenset({"post-pub-bad"}))
    report = poll_due_metrics(store=store, pollers={"youtube": poller}, now=_at(6))
    assert (report.failed, report.observed) == (1, 1)
    assert store.read_observation(publication_id="pub-good", window_index=0)
    # 실패한 건의 창은 비어 있다 — 결측으로 지어내지 않는다.
    assert store.read_observation(publication_id="pub-bad", window_index=0) == ()


def test_adapter_error_leaves_the_reason_in_the_ledger() -> None:
    store = _store(_item("pub-bad"))
    poll_due_metrics(
        store=store,
        pollers={"youtube": FakePoller(fail_for=frozenset({"post-pub-bad"}))},
        now=_at(6),
    )
    errors = [e for e in store.events if e["kind"] == "error"]
    assert len(errors) == 1
    payload = errors[0]["payload"]
    assert payload["reason"] == "metrics_poll_failed"  # type: ignore[index]
    assert "quota exceeded" in payload["error"]  # type: ignore[index,operator]


def test_unrouted_platform_is_counted_not_raised() -> None:
    """IG 폴러(IG-3)가 아직 없다고 유튜브 지표 수집이 멈추면 안 된다."""
    store = _store(_item("pub-ig", platform="instagram"), _item("pub-yt"))
    poller = FakePoller()
    report = poll_due_metrics(store=store, pollers={"youtube": poller}, now=_at(6))
    assert (report.unrouted, report.observed) == (1, 1)
    assert poller.calls == [("post-pub-yt", 0)]
    # 창은 그대로 남는다 — 어댑터가 붙으면 다음 훑기에 잡힌다.
    assert store.read_observation(publication_id="pub-ig", window_index=0) == ()


def test_unrouted_platform_leaves_no_ledger_noise() -> None:
    """훑기마다 같은 사실을 다시 쓰면 append-only 원장이 그것으로 채워진다."""
    store = _store(_item("pub-ig", platform="instagram"))
    for _ in range(3):
        poll_due_metrics(store=store, pollers={"youtube": FakePoller()}, now=_at(6))
    assert store.events == []


# ── 놓침·지평 ───────────────────────────────────────────────────────


def test_late_window_is_counted_as_missed_and_not_polled() -> None:
    store = _store(_item())
    poller = FakePoller()
    report = poll_due_metrics(store=store, pollers={"youtube": poller}, now=_at(20))
    assert (report.missed, report.observed) == (1, 0)
    assert poller.calls == []
    assert store.events == []  # 결정론이라 매 훑기가 같은 사실을 반복한다 — 적지 않는다.


def test_publication_beyond_horizon_is_not_swept() -> None:
    store = _store(_item(offset_h=0))
    poller = FakePoller()
    report = poll_due_metrics(
        store=store, pollers={"youtube": poller}, now=_at(24 * 30), horizon_days=14
    )
    assert (report.items, report.observed) == (0, 0)
    assert poller.calls == []


def test_report_summary_is_readable() -> None:
    store = _store(_item())
    report = poll_due_metrics(store=store, pollers={"youtube": FakePoller()}, now=_at(6))
    assert "대상 1건" in report.summary()
    assert "적재 1" in report.summary()
