"""C5 발행 store·러너 통합 테스트용 픽스처 (라이브 PostgreSQL).

`PgPublishAttemptStore`는 autocommit 커넥션을 가정하므로 `db`도 autocommit이다.
스키마는 세션 1회 재생성, 데이터는 테스트마다 TRUNCATE로 격리한다.

**개발 DB는 건드리지 않는다**: `DATABASE_URL`을 그대로 쓰지 않고 dbname에 `_test`를
붙인 별도 DB를 쓴다([dbguard]). 없으면 만든다 — 팀원이 환경변수를 따로 외우거나
compose에 서비스를 더할 필요가 없다. 파괴적 작업 직전에 가드를 한 번 더 확인한다.
"""

import os
import uuid
from collections.abc import Callable, Iterator

import psycopg
import pytest

from sns.db.migrate import apply_migrations
from tests.dbguard import admin_dsn_for, database_name, derive_test_dsn, require_test_dsn

DSN = derive_test_dsn(os.environ.get("DATABASE_URL", "postgresql://sns:sns@localhost:5432/sns"))

_MUTABLE_TABLES = (
    "channel, cycle, topic, content_item, media_asset, publication, publish_attempt, run_event"
)

# channel~publication FK 체인 + (선택) media_asset을 한 번에 만들고 publication id 반환.
# content_item.status는 명시 세팅(테이블 기본 'draft' 의존 금지) — 러너의 hybrid
# 콘텐츠 승인 관문(FR-Q3)이 이 값을 보므로, 기본값 'approved'로 대부분 테스트가
# 승인 완료 상태를 가정하게 하고 그 관문 자체를 검증하는 테스트만 오버라이드한다.
_SEED_WITH_MEDIA = """
WITH ch AS (
    INSERT INTO channel (platform, handle, mode)
    VALUES (%(platform)s, %(handle)s, %(mode)s) RETURNING id
), cy AS (
    INSERT INTO cycle (goal_ref) VALUES ('test-goal') RETURNING id
), tp AS (
    INSERT INTO topic (title) VALUES ('test-topic') RETURNING id
), ci AS (
    INSERT INTO content_item (cycle_id, topic_id, format, body, status)
    SELECT cy.id, tp.id, %(fmt)s, %(body)s, %(content_status)s FROM cy, tp RETURNING id
), ma AS (
    INSERT INTO media_asset (content_item_id, kind, storage_url, checksum, quality_status)
    SELECT ci.id, %(kind)s, %(storage_url)s, %(checksum)s, %(qstatus)s FROM ci RETURNING id
)
INSERT INTO publication (content_item_id, channel_id)
SELECT ci.id, ch.id FROM ci, ch RETURNING id
"""

# media_asset 없이 발행 건만 (러너 no_media 경로).
_SEED_NO_MEDIA = """
WITH ch AS (
    INSERT INTO channel (platform, handle, mode)
    VALUES (%(platform)s, %(handle)s, %(mode)s) RETURNING id
), cy AS (
    INSERT INTO cycle (goal_ref) VALUES ('test-goal') RETURNING id
), tp AS (
    INSERT INTO topic (title) VALUES ('test-topic') RETURNING id
), ci AS (
    INSERT INTO content_item (cycle_id, topic_id, format, body, status)
    SELECT cy.id, tp.id, %(fmt)s, %(body)s, %(content_status)s FROM cy, tp RETURNING id
)
INSERT INTO publication (content_item_id, channel_id)
SELECT ci.id, ch.id FROM ci, ch RETURNING id
"""


def _ensure_test_database() -> None:
    """테스트 DB가 없으면 만든다. 서버 자체가 없으면 skip(기존 동작 유지)."""
    try:
        admin = psycopg.connect(admin_dsn_for(DSN), connect_timeout=5, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL 미가동 — docker compose up -d postgres")
    name = database_name(DSN)
    with admin:
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if exists is None:
            # 식별자는 파라미터 바인딩이 안 되지만, name은 DATABASE_URL에서 우리가
            # 파생시킨 값이고 require_test_dsn을 이미 통과했다.
            admin.execute(f'CREATE DATABASE "{name}"')


@pytest.fixture(scope="session")
def _schema() -> Iterator[None]:
    # 개발 DB를 지우는 사고 방어 — DROP/TRUNCATE 직전 마지막 확인.
    require_test_dsn(DSN)
    _ensure_test_database()
    try:
        c = psycopg.connect(DSN, connect_timeout=5, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL 미가동 — docker compose up -d postgres")
    with c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")
        apply_migrations(c)
        yield


@pytest.fixture
def db(_schema: None) -> Iterator[psycopg.Connection]:
    c = psycopg.connect(DSN, autocommit=True)
    with c:
        c.execute(f"TRUNCATE {_MUTABLE_TABLES} RESTART IDENTITY CASCADE")
        yield c


SeedFn = Callable[..., str]


@pytest.fixture
def seed(db: psycopg.Connection) -> SeedFn:
    """발행 대기 publication 1건을 시드하고 그 id(str)를 돌려준다."""

    def _seed(
        *,
        quality_status: str = "passed",
        kind: str = "video",
        fmt: str = "reels",
        body: str = "훅 문장\n본문",
        platform: str = "instagram",
        checksum: str = "chk-seed",
        with_media: bool = True,
        mode: str = "auto",
        content_status: str = "approved",
    ) -> str:
        params = {
            "platform": platform,
            "mode": mode,
            "handle": f"h-{uuid.uuid4().hex[:8]}",
            "fmt": fmt,
            "body": body,
            "kind": kind,
            "storage_url": f"mem://{checksum}",
            "checksum": checksum,
            "qstatus": quality_status,
            "content_status": content_status,
        }
        sql = _SEED_WITH_MEDIA if with_media else _SEED_NO_MEDIA
        row = db.execute(sql, params).fetchone()
        assert row is not None
        return str(row[0])

    return _seed
