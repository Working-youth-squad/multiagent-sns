import os
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg import errors

from sns.db.migrate import apply_migrations
from tests.dbguard import derive_test_dsn, require_test_dsn

# conftest와 같은 규칙 — 개발 DB가 아니라 `_test` DB를 쓴다(이 파일은 스키마를 DROP한다).
DSN = derive_test_dsn(os.environ.get("DATABASE_URL", "postgresql://sns:sns@localhost:5432/sns"))

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
    INSERT INTO channel (platform, handle)
    VALUES ('instagram', %(handle)s) RETURNING id
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
        require_test_dsn(DSN)  # DROP SCHEMA 직전 방어
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


def test_publish_mode_columns_dropped(conn: psycopg.Connection) -> None:
    # 003: 발행 모드 3분류(수동·반자동·자동)와 사람 개입 기록을 제거했다.
    rows = conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND (table_name, column_name) IN"
        " (('channel', 'mode'), ('publication', 'mode'), ('content_item', 'edited_by_human'))"
    ).fetchall()
    assert rows == []


def test_needs_review_status_rejected(conn: psycopg.Connection) -> None:
    # 사람 승인 관문이 없으므로 needs_review는 어느 테이블에서도 허용되지 않는다.
    pub_id = _seed_publication(conn)
    row = conn.execute(
        "SELECT content_item_id FROM publication WHERE id = %s", (pub_id,)
    ).fetchone()
    assert row is not None
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute("UPDATE content_item SET status = 'needs_review' WHERE id = %s", (row[0],))
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO media_asset (content_item_id, kind, storage_url, checksum,"
            " quality_status) VALUES (%s, 'image', 's3://x', 'abc', 'needs_review')",
            (row[0],),
        )


def test_quality_status_defaults_to_failed(conn: psycopg.Connection) -> None:
    # fail-closed: 게이트가 passed를 쓰기 전까지 발행에 진입하지 못한다.
    pub_id = _seed_publication(conn)
    row = conn.execute(
        "SELECT content_item_id FROM publication WHERE id = %s", (pub_id,)
    ).fetchone()
    assert row is not None
    asset = conn.execute(
        "INSERT INTO media_asset (content_item_id, kind, storage_url, checksum)"
        " VALUES (%s, 'image', 's3://x', 'abc') RETURNING quality_status",
        (row[0],),
    ).fetchone()
    conn.commit()
    assert asset is not None
    assert asset[0] == "failed"


def test_duplicate_external_post_per_channel_rejected(conn: psycopg.Connection) -> None:
    # 002 부분 유니크 인덱스는 003 이후에도 남는다 — 같은 채널에 같은 외부 게시물
    # id가 두 번 기록되는 것을 막는 방어는 기계 발행에도 유효하다.
    pub_id = _seed_publication(conn)
    conn.execute("UPDATE publication SET external_post_id = 'ext-dup' WHERE id = %s", (pub_id,))
    conn.commit()
    row = conn.execute(
        "SELECT channel_id, content_item_id FROM publication WHERE id = %s", (pub_id,)
    ).fetchone()
    assert row is not None
    ci2 = conn.execute(
        "INSERT INTO content_item (cycle_id, topic_id, format) "
        "SELECT cycle_id, topic_id, format FROM content_item WHERE id = %s RETURNING id",
        (row[1],),
    ).fetchone()
    assert ci2 is not None
    conn.commit()
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        conn.execute(
            "INSERT INTO publication (content_item_id, channel_id, external_post_id) "
            "VALUES (%s, %s, 'ext-dup')",
            (ci2[0], row[0]),
        )


def test_publish_attempt_invalid_state_rejected(conn: psycopg.Connection) -> None:
    pub_id = _seed_publication(conn)
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO publish_attempt (publication_id, state) VALUES (%s, 'bogus')",
            (pub_id,),
        )
