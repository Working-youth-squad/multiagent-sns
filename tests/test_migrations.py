import os
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg import errors

from sns.db.migrate import apply_migrations

DSN = os.environ.get("DATABASE_URL", "postgresql://sns:sns@localhost:5432/sns")

EXPECTED_TABLES = {
    "schema_version",
    "channel",
    "cycle",
    "topic",
    "topic_stats",
    "content_item",
    "media_asset",
    "publication",
    "publish_attempt",
    "metric_observation",
    "metric_value",
    "reward",
    "playbook",
    "analysis_note",
    "run_event",
}

# channel~publication FK 체인을 한 번에 만들고 publication id를 돌려준다
SEED_PUBLICATION_SQL = """
WITH ch AS (
    INSERT INTO channel (platform, handle, mode)
    VALUES ('instagram', %(handle)s, 'auto') RETURNING id
), cy AS (
    INSERT INTO cycle (goal_ref) VALUES ('test-goal') RETURNING id
), tp AS (
    INSERT INTO topic (title) VALUES ('test-topic') RETURNING id
), ci AS (
    INSERT INTO content_item (cycle_id, topic_id, format)
    SELECT cy.id, tp.id, 'reels' FROM cy, tp RETURNING id
)
INSERT INTO publication (content_item_id, channel_id)
SELECT ci.id, ch.id FROM ci, ch RETURNING id
"""


@pytest.fixture(scope="module")
def conn() -> Iterator[psycopg.Connection]:
    try:
        c = psycopg.connect(DSN, connect_timeout=5)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL 미가동 — docker compose up -d postgres")
    with c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")
        c.commit()
        apply_migrations(c)
        yield c


def _seed_publication(conn: psycopg.Connection) -> Any:
    row = conn.execute(SEED_PUBLICATION_SQL, {"handle": f"h-{uuid.uuid4().hex[:8]}"}).fetchone()
    assert row is not None
    conn.commit()
    return row[0]


def _seed_observation(conn: psycopg.Connection) -> Any:
    row = conn.execute(
        "INSERT INTO metric_observation (publication_id, window_index) VALUES (%s, 0) RETURNING id",
        (_seed_publication(conn),),
    ).fetchone()
    assert row is not None
    conn.commit()
    return row[0]


def test_all_tables_exist(conn: psycopg.Connection) -> None:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    assert {r[0] for r in rows} == EXPECTED_TABLES


def test_rerun_is_idempotent(conn: psycopg.Connection) -> None:
    assert apply_migrations(conn) == []


def test_metric_value_accepts_valid_rows(conn: psycopg.Connection) -> None:
    obs_id = _seed_observation(conn)
    conn.execute(
        "INSERT INTO metric_value (observation_id, metric_key, value, missing)"
        " VALUES (%s, 'reach', 123, false), (%s, 'skip_rate', NULL, true)",
        (obs_id, obs_id),
    )
    conn.commit()


def test_metric_missing_xor_rejected(conn: psycopg.Connection) -> None:
    obs_id = _seed_observation(conn)
    # missing=true인데 value가 있으면 거부 (NFR-3)
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO metric_value (observation_id, metric_key, value, missing)"
            " VALUES (%s, 'reach', 1, true)",
            (obs_id,),
        )
    # missing=false인데 value가 NULL이어도 거부
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO metric_value (observation_id, metric_key, value, missing)"
            " VALUES (%s, 'reach', NULL, false)",
            (obs_id,),
        )


def test_publish_attempt_invalid_state_rejected(conn: psycopg.Connection) -> None:
    pub_id = _seed_publication(conn)
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO publish_attempt (publication_id, state) VALUES (%s, 'bogus')",
            (pub_id,),
        )
