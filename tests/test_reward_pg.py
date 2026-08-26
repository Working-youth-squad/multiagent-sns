"""보상 배치 × PgMetricStore 관통 — 관측에서 시작해 reward·topic_stats까지 (FR-L2, M6).

순수 테스트(test_reward)가 산식의 분기를 다 겨누므로 여기서는 **DB만 아는 사실** 셋을
본다: NULL 보상이 실제로 NULL로 앉는가, `topic_stats`가 발행 원장의 (주제×포맷×채널)로
집계되는가, 재실행이 그 통계를 부풀리지 않는가.
"""

from datetime import UTC, datetime

import psycopg

from sns.learning.reward import formula_version, run_reward_batch
from sns.learning.schedule import REWARD_WINDOW_INDEX
from sns.learning.stores import PgMetricStore
from sns.tools.contracts import MetricValue
from tests.conftest import SeedFn

GOAL = "engagement_depth"
# 참여 깊이(IG)가 보는 지표 — 비율 셋의 분모까지 함께.
FULL_IG = (
    MetricValue("reach", 1000.0, False),
    MetricValue("shares", 30.0, False),
    MetricValue("saved", 50.0, False),
    MetricValue("likes", 200.0, False),
    MetricValue("views", 5000.0, False),
)
ALL_MISSING = tuple(MetricValue(v.metric_key, None, True) for v in FULL_IG)


def _published_with_window(
    db: psycopg.Connection, pub_id: str, *, post_id: str, values: tuple[MetricValue, ...]
) -> str:
    """발행 완료 + 대표 창(72h) 관측까지 — 보상 배치가 물 수 있는 최소 상태."""
    db.execute(
        """
        UPDATE publication
           SET status = 'published', external_post_id = %s,
               published_at = now() - make_interval(hours => 80)
         WHERE id = %s
        """,
        (post_id, pub_id),
    )
    PgMetricStore(db).save_observation(
        publication_id=pub_id, window_index=REWARD_WINDOW_INDEX, values=values
    )
    return pub_id


def test_보상과_통계가_한_번에_선다(db: psycopg.Connection, seed: SeedFn) -> None:
    pub_id = _published_with_window(db, seed(), post_id="ig-1", values=FULL_IG)

    report = run_reward_batch(store=PgMetricStore(db), now=datetime.now(UTC), goal_ref=GOAL)

    assert (report.computed, report.pending) == (1, 0)
    row = db.execute(
        "SELECT reward_value, formula_version FROM reward WHERE publication_id = %s", (pub_id,)
    ).fetchone()
    assert row is not None and row[0] is not None and row[1] == formula_version(GOAL)
    stats = db.execute("SELECT format, platform, trials, reward_sum FROM topic_stats").fetchall()
    assert stats == [("reels", "instagram", 1, row[0])]


def test_표본_부족은_NULL로_앉는다(db: psycopg.Connection, seed: SeedFn) -> None:
    """폴링은 됐고 값이 전부 결측인 창 — 0이 아니라 NULL이어야 밴딧이 건너뛴다(FR-L2)."""
    pub_id = _published_with_window(db, seed(), post_id="ig-2", values=ALL_MISSING)

    report = run_reward_batch(store=PgMetricStore(db), now=datetime.now(UTC), goal_ref=GOAL)

    assert (report.insufficient, report.computed) == (1, 0)
    row = db.execute(
        "SELECT reward_value FROM reward WHERE publication_id = %s", (pub_id,)
    ).fetchone()
    assert row == (None,)
    assert db.execute("SELECT count(*) FROM topic_stats").fetchone() == (0,)


def test_두_번_돌아도_통계가_자라지_않는다(db: psycopg.Connection, seed: SeedFn) -> None:
    """스케줄러가 하루 한 번 부르는 배치다 — 재실행이 성과를 두 배로 만들면 안 된다."""
    _published_with_window(db, seed(), post_id="ig-3", values=FULL_IG)
    store = PgMetricStore(db)
    now = datetime.now(UTC)
    first = run_reward_batch(store=store, now=now, goal_ref=GOAL)
    second = run_reward_batch(store=store, now=now, goal_ref=GOAL)
    forced = run_reward_batch(store=store, now=now, goal_ref=GOAL, recompute=True)

    assert (first.computed, second.absorbed, forced.computed) == (1, 1, 1)
    stats = db.execute("SELECT trials FROM topic_stats").fetchall()
    assert stats == [(1,)]
    assert db.execute("SELECT count(*) FROM reward").fetchone() == (1,)
