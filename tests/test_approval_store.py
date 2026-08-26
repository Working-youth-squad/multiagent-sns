"""ApprovalStore Pg 백엔드 — 실 PostgreSQL 통합 검증(승인/반려의 발행 게이트 반전)."""

import psycopg
import pytest

from sns.web.approve.store import ApprovalNotFound, PgApprovalStore

# 채널~publication FK 체인을 hybrid·needs_review 초안으로 채운다(cycle.run_cycle의
# hybrid 배선과 동형: content_item.status='needs_review', media_asset.quality_status
# 는 게이트 미배선 기본값 'needs_review').
_SEED = """
WITH ch AS (
    INSERT INTO channel (platform, handle, mode) VALUES ('instagram', %(handle)s, 'hybrid')
    RETURNING id
), cy AS (
    INSERT INTO cycle (goal_ref) VALUES ('test-goal') RETURNING id
), tp AS (
    INSERT INTO topic (title) VALUES (%(title)s) RETURNING id
), ci AS (
    INSERT INTO content_item (cycle_id, topic_id, format, status, body)
    SELECT cy.id, tp.id, 'feed_image', 'needs_review', %(body)s FROM cy, tp
    RETURNING id
), ma AS (
    INSERT INTO media_asset (content_item_id, kind, storage_url, checksum, quality_status)
    SELECT ci.id, 'image', 'mem://seed', 'chk-approve', 'needs_review' FROM ci
    RETURNING id
), pub AS (
    INSERT INTO publication (content_item_id, channel_id, mode)
    SELECT ci.id, ch.id, 'hybrid' FROM ci, ch
    RETURNING id
)
SELECT ci.id, ma.id, pub.id FROM ci, ma, pub
"""


def _seed_hybrid(
    db: psycopg.Connection, *, title: str = "테스트 주제", body: str = "훅\n본문"
) -> tuple[str, str, str]:
    row = db.execute(
        _SEED, {"handle": f"h-approve-{title}", "title": title, "body": body}
    ).fetchone()
    assert row is not None
    return str(row[0]), str(row[1]), str(row[2])


def _publication_status(db: psycopg.Connection, publication_id: str) -> str:
    row = db.execute("SELECT status FROM publication WHERE id = %s", (publication_id,)).fetchone()
    assert row is not None
    return str(row[0])


def test_list_pending_finds_needs_review_hybrid_item(db: psycopg.Connection) -> None:
    ci_id, _, _ = _seed_hybrid(db, title="가을 산책")
    store = PgApprovalStore(db)
    items = store.list_pending()
    assert [i.content_item_id for i in items] == [ci_id]
    assert items[0].topic_title == "가을 산책"
    assert items[0].platform == "instagram"


def test_list_pending_excludes_auto_channel(db: psycopg.Connection) -> None:
    db.execute(
        "WITH ch AS (INSERT INTO channel (platform, handle, mode) "
        "VALUES ('instagram', 'auto-ch', 'auto') RETURNING id), "
        "cy AS (INSERT INTO cycle (goal_ref) VALUES ('g') RETURNING id), "
        "tp AS (INSERT INTO topic (title) VALUES ('auto-topic') RETURNING id), "
        "ci AS (INSERT INTO content_item (cycle_id, topic_id, format, status, body) "
        "SELECT cy.id, tp.id, 'feed_image', 'approved', 'b' FROM cy, tp RETURNING id) "
        "INSERT INTO publication (content_item_id, channel_id, mode) "
        "SELECT ci.id, ch.id, 'auto' FROM ci, ch"
    )
    assert PgApprovalStore(db).list_pending() == ()


def test_get_pending_returns_none_for_missing(db: psycopg.Connection) -> None:
    assert PgApprovalStore(db).get_pending("00000000-0000-0000-0000-000000000000") is None


def test_approve_sets_media_passed_and_content_approved(db: psycopg.Connection) -> None:
    ci_id, ma_id, pub_id = _seed_hybrid(db)
    PgApprovalStore(db).approve(ci_id, body="훅\n본문")  # 원문과 동일 — 미수정

    status, edited = db.execute(
        "SELECT status, edited_by_human FROM content_item WHERE id = %s", (ci_id,)
    ).fetchone()
    assert status == "approved"
    assert edited is False
    qstatus = db.execute(
        "SELECT quality_status FROM media_asset WHERE id = %s", (ma_id,)
    ).fetchone()[0]
    assert qstatus == "passed"
    # 승인 즉시 대기 목록에서 사라진다(=러너가 발행 진입 가능).
    assert PgApprovalStore(db).get_pending(ci_id) is None


def test_approve_with_edited_body_sets_edited_flag(db: psycopg.Connection) -> None:
    ci_id, _, _ = _seed_hybrid(db, body="원본")
    PgApprovalStore(db).approve(ci_id, body="수정됨")

    body, edited = db.execute(
        "SELECT body, edited_by_human FROM content_item WHERE id = %s", (ci_id,)
    ).fetchone()
    assert body == "수정됨"
    assert edited is True


def test_reject_sets_media_failed_and_publication_skipped(db: psycopg.Connection) -> None:
    ci_id, ma_id, pub_id = _seed_hybrid(db)
    PgApprovalStore(db).reject(ci_id, reason="톤 부적절")

    status = db.execute("SELECT status FROM content_item WHERE id = %s", (ci_id,)).fetchone()[0]
    assert status == "rejected"
    qstatus = db.execute(
        "SELECT quality_status FROM media_asset WHERE id = %s", (ma_id,)
    ).fetchone()[0]
    assert qstatus == "failed"
    assert _publication_status(db, pub_id) == "skipped"


def test_approve_logs_run_event(db: psycopg.Connection) -> None:
    ci_id, _, _ = _seed_hybrid(db)
    PgApprovalStore(db).approve(ci_id, body="훅\n본문")
    row = db.execute(
        "SELECT payload->>'reason', payload->>'content_item_id' FROM run_event "
        "WHERE kind = 'notice'"
    ).fetchone()
    assert row == ("hybrid_approved", ci_id)


def test_update_media_replaces_spec_and_asset_but_keeps_pending(db: psycopg.Connection) -> None:
    """재렌더는 항목을 종결하지 않는다 — 새 spec·미디어로 갱신 후 다시 승인 대기."""
    ci_id, ma_id, _ = _seed_hybrid(db, title="재렌더 대상")
    PgApprovalStore(db).update_media(
        ci_id,
        media_spec={"topic": "고친 주제"},
        storage_url="mem://rerendered",
        checksum="chk-2",
        quality_status="passed",
        quality_report={"passed": True, "failures": []},
    )
    spec, edited = db.execute(
        "SELECT media_spec, edited_by_human FROM content_item WHERE id = %s", (ci_id,)
    ).fetchone()
    assert spec == {"topic": "고친 주제"}
    assert edited is True
    url, chk, qstatus = db.execute(
        "SELECT storage_url, checksum, quality_status FROM media_asset WHERE id = %s", (ma_id,)
    ).fetchone()
    assert (url, chk, qstatus) == ("mem://rerendered", "chk-2", "passed")
    item = PgApprovalStore(db).get_pending(ci_id)
    assert item is not None and item.media_spec == {"topic": "고친 주제"}
    row = db.execute("SELECT payload->>'reason' FROM run_event WHERE kind = 'notice'").fetchone()
    assert row == ("hybrid_rerendered",)


def test_update_media_missing_raises_not_found(db: psycopg.Connection) -> None:
    with pytest.raises(ApprovalNotFound):
        PgApprovalStore(db).update_media(
            "00000000-0000-0000-0000-000000000000",
            media_spec={},
            storage_url="mem://x",
            checksum="c",
            quality_status="passed",
            quality_report=None,
        )


def test_double_approve_raises_not_found(db: psycopg.Connection) -> None:
    ci_id, _, _ = _seed_hybrid(db)
    store = PgApprovalStore(db)
    store.approve(ci_id, body="훅\n본문")
    with pytest.raises(ApprovalNotFound):
        store.approve(ci_id, body="훅\n본문")


def test_double_reject_raises_not_found(db: psycopg.Connection) -> None:
    ci_id, _, _ = _seed_hybrid(db)
    store = PgApprovalStore(db)
    store.reject(ci_id, reason="사유")
    with pytest.raises(ApprovalNotFound):
        store.reject(ci_id, reason="사유2")
