"""폴러 × PgMetricStore 관통 — 발행 원장에서 시작해 metric_value까지 (FR-L1, M6).

순수 테스트(test_metrics_poller)가 분기를 다 겨누므로 여기서는 **관통 한 줄**만 본다:
실제 발행 원장을 훑어 창을 고르고, 값이 테이블에 앉고, `run_event`에 흔적이 남는가.
"""

from datetime import UTC, datetime

import psycopg

from sns.learning.poller import poll_due_metrics
from sns.learning.stores import PgMetricStore
from sns.tools.contracts import MetricValue, Platform
from tests.conftest import SeedFn


class FakePoller:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def __call__(
        self, platform: Platform, post_id: str, window_index: int
    ) -> tuple[MetricValue, ...]:
        self.calls.append((post_id, window_index))
        return (MetricValue("views", 42.0, False), MetricValue("avg_view_pct", None, True))


def _publish(db: psycopg.Connection, pub_id: str, *, post_id: str, hours_ago: int) -> str:
    db.execute(
        """
        UPDATE publication
           SET status = 'published', external_post_id = %s,
               published_at = now() - make_interval(hours => %s)
         WHERE id = %s
        """,
        (post_id, hours_ago, pub_id),
    )
    return pub_id


def test_sweep_lands_values_in_the_ledger(db: psycopg.Connection, seed: SeedFn) -> None:
    pub_id = _publish(db, seed(platform="youtube"), post_id="vid-1", hours_ago=7)
    poller = FakePoller()
    report = poll_due_metrics(
        store=PgMetricStore(db), pollers={"youtube": poller}, now=datetime.now(UTC)
    )
    assert poller.calls == [("vid-1", 0)]
    assert report.observed == 1
    rows = db.execute(
        """
        SELECT mv.metric_key, mv.value, mv.missing
          FROM metric_value mv
          JOIN metric_observation mo ON mo.id = mv.observation_id
         WHERE mo.publication_id = %s AND mo.window_index = 0
         ORDER BY mv.metric_key
        """,
        (pub_id,),
    ).fetchall()
    assert rows == [("avg_view_pct", None, True), ("views", 42.0, False)]
    kinds = db.execute("SELECT kind FROM run_event").fetchall()
    assert kinds == [("metric_polled",)]


def test_second_sweep_of_the_same_window_changes_nothing(
    db: psycopg.Connection, seed: SeedFn
) -> None:
    """스케줄러가 시간마다 부르는 배치다 — 두 번 돌아도 원장이 자라지 않아야 한다."""
    _publish(db, seed(platform="youtube"), post_id="vid-1", hours_ago=7)
    store = PgMetricStore(db)
    for _ in range(2):
        poll_due_metrics(store=store, pollers={"youtube": FakePoller()}, now=datetime.now(UTC))
    counts = db.execute(
        "SELECT (SELECT count(*) FROM metric_observation), (SELECT count(*) FROM metric_value)"
    ).fetchone()
    assert counts == (1, 2)


def test_instagram_publication_waits_for_its_adapter(db: psycopg.Connection, seed: SeedFn) -> None:
    """IG 폴러(IG-3)가 없어도 훑기는 성공하고, 그 건의 창은 손대지 않은 채 남는다."""
    pub_id = _publish(db, seed(platform="instagram"), post_id="ig-1", hours_ago=7)
    report = poll_due_metrics(
        store=PgMetricStore(db), pollers={"youtube": FakePoller()}, now=datetime.now(UTC)
    )
    assert (report.items, report.unrouted, report.observed) == (1, 1, 0)
    empty = db.execute(
        "SELECT count(*) FROM metric_observation WHERE publication_id = %s", (pub_id,)
    ).fetchone()
    assert empty is not None and empty[0] == 0
