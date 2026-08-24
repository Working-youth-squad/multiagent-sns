"""수동(manual) 발행 등록 경계 검증 — FR-E5, [sns.publish.manual].

라이브 PostgreSQL 통합 테스트(conftest `db` 픽스처, autocommit).
"""

import uuid

import psycopg
import pytest

from sns.publish.manual import ManualRegistrationError, register_manual_publication


def _seed_context(db: psycopg.Connection, *, channel_mode: str = "manual") -> dict[str, str]:
    """채널 + 사이클 + 주제(같은 프롬프트 묶음의 최소 컨텍스트)를 만든다."""
    ch = db.execute(
        "INSERT INTO channel (platform, handle, mode) VALUES ('instagram', %s, %s) RETURNING id",
        (f"h-{uuid.uuid4().hex[:8]}", channel_mode),
    ).fetchone()
    cy = db.execute("INSERT INTO cycle (goal_ref) VALUES ('test-goal') RETURNING id").fetchone()
    tp = db.execute("INSERT INTO topic (title) VALUES ('test-topic') RETURNING id").fetchone()
    assert ch and cy and tp
    return {"channel_id": str(ch[0]), "cycle_id": str(cy[0]), "topic_id": str(tp[0])}


def _register(db: psycopg.Connection, ctx: dict[str, str], post_id: str = "ig-123"):
    return register_manual_publication(
        db,
        channel_id=ctx["channel_id"],
        cycle_id=ctx["cycle_id"],
        topic_id=ctx["topic_id"],
        content_format="feed_image",
        body="사람이 직접 쓴 본문",
        external_post_id=post_id,
    )


def test_registers_published_publication_with_manual_mode(db: psycopg.Connection) -> None:
    ctx = _seed_context(db)
    reg = _register(db, ctx)

    assert not reg.already_registered
    row = db.execute(
        "SELECT status, mode, external_post_id, published_at FROM publication WHERE id = %s",
        (reg.publication_id,),
    ).fetchone()
    assert row is not None
    status, mode, ext_id, published_at = row
    # 증빙의 핵심: 발행 행 자체에 mode='manual' 스냅샷이 남는다.
    assert (status, mode, ext_id) == ("published", "manual", "ig-123")
    assert published_at is not None

    ci = db.execute(
        "SELECT status, body, edited_by_human FROM content_item WHERE id = %s",
        (reg.content_item_id,),
    ).fetchone()
    assert ci == ("approved", "사람이 직접 쓴 본문", True)

    ev = db.execute(
        "SELECT payload->>'mode', payload->>'registered_manual' FROM run_event "
        "WHERE kind = 'publish_attempted'"
    ).fetchone()
    assert ev == ("manual", "true")


def test_reregistration_is_idempotent(db: psycopg.Connection) -> None:
    ctx = _seed_context(db)
    first = _register(db, ctx)
    second = _register(db, ctx)

    assert second.already_registered
    assert second.publication_id == first.publication_id
    (count,) = db.execute("SELECT count(*) FROM publication").fetchone() or (0,)
    assert count == 1


def test_machine_channel_registration_rejected(db: psycopg.Connection) -> None:
    # auto/hybrid 채널에 수동 등록 = 실험군 오염 → 거부.
    ctx = _seed_context(db, channel_mode="auto")
    with pytest.raises(ManualRegistrationError):
        _register(db, ctx)
    (count,) = db.execute("SELECT count(*) FROM publication").fetchone() or (0,)
    assert count == 0


def test_unknown_channel_rejected(db: psycopg.Connection) -> None:
    ctx = _seed_context(db)
    ctx["channel_id"] = str(uuid.uuid4())
    with pytest.raises(ManualRegistrationError):
        _register(db, ctx)


def test_empty_external_post_id_rejected(db: psycopg.Connection) -> None:
    ctx = _seed_context(db)
    with pytest.raises(ManualRegistrationError):
        _register(db, ctx, post_id="")
