"""PgPublishAttemptStore 통합 검증 — 원장(publish_attempt)과 발행 상태(publication)를
한 트랜잭션에서 함께 갱신하는지, 라운드트립이 정확한지 (C5 후속, FR-P3)."""

import psycopg

from sns.publish.state_machine import PublishAttempt
from sns.publish.stores import PgPublishAttemptStore
from tests.conftest import SeedFn


def _publication(db: psycopg.Connection, pub_id: str) -> tuple:
    row = db.execute(
        "SELECT status, external_post_id, published_at FROM publication WHERE id = %s",
        (pub_id,),
    ).fetchone()
    assert row is not None
    return row


def _attempt_count(db: psycopg.Connection, pub_id: str) -> int:
    row = db.execute(
        "SELECT count(*) FROM publish_attempt WHERE publication_id = %s", (pub_id,)
    ).fetchone()
    assert row is not None
    return row[0]


def test_load_missing_returns_none(db: psycopg.Connection, seed: SeedFn) -> None:
    store = PgPublishAttemptStore(db)
    assert store.load(seed()) is None


def test_roundtrip_pending(db: psycopg.Connection, seed: SeedFn) -> None:
    pub_id = seed()
    store = PgPublishAttemptStore(db)
    store.save(PublishAttempt(publication_id=pub_id, state="pending"))
    assert store.load(pub_id) == PublishAttempt(publication_id=pub_id, state="pending")
    # pending은 publication.status를 건드리지 않는다.
    assert _publication(db, pub_id)[0] == "pending"


def test_container_created_preserves_container_id(db: psycopg.Connection, seed: SeedFn) -> None:
    pub_id = seed()
    store = PgPublishAttemptStore(db)
    store.save(
        PublishAttempt(publication_id=pub_id, state="container_created", container_id="ig-c1")
    )
    loaded = store.load(pub_id)
    assert loaded is not None
    assert loaded.state == "container_created"
    assert loaded.container_id == "ig-c1"
    assert _publication(db, pub_id)[0] == "pending"  # 아직 발행 전


def test_published_updates_publication_atomically(db: psycopg.Connection, seed: SeedFn) -> None:
    pub_id = seed()
    store = PgPublishAttemptStore(db)
    store.save(
        PublishAttempt(
            publication_id=pub_id, state="published", external_post_id="IG_123", container_id="c1"
        )
    )
    loaded = store.load(pub_id)
    assert loaded is not None
    assert loaded.state == "published"
    assert loaded.external_post_id == "IG_123"

    status, external, published_at = _publication(db, pub_id)
    assert status == "published"
    assert external == "IG_123"
    assert published_at is not None


def test_failed_marks_publication_failed(db: psycopg.Connection, seed: SeedFn) -> None:
    pub_id = seed()
    store = PgPublishAttemptStore(db)
    store.save(
        PublishAttempt(
            publication_id=pub_id,
            state="failed",
            error_class="auth",
            error_raw="토큰 만료",
        )
    )
    loaded = store.load(pub_id)
    assert loaded is not None
    assert loaded.state == "failed"
    assert loaded.error_class == "auth"
    assert loaded.error_raw == "토큰 만료"
    # 실패 원장은 external_post_id를 싣지 않는다(published일 때만).
    assert loaded.external_post_id is None
    assert _publication(db, pub_id)[0] == "failed"


def test_save_upserts_single_row(db: psycopg.Connection, seed: SeedFn) -> None:
    # UNIQUE(publication_id) 위에서 상태 전이는 새 행이 아니라 갱신이어야 한다.
    pub_id = seed()
    store = PgPublishAttemptStore(db)
    store.save(PublishAttempt(publication_id=pub_id, state="pending"))
    store.save(PublishAttempt(publication_id=pub_id, state="container_created", container_id="c1"))
    store.save(PublishAttempt(publication_id=pub_id, state="published", external_post_id="X"))
    assert _attempt_count(db, pub_id) == 1
    loaded = store.load(pub_id)
    assert loaded is not None and loaded.state == "published"
