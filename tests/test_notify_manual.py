"""수동 배정 알림 폴러 — InMemory(순수 로직) + Pg(멱등·SQL) 양쪽 검증(FR-E5)."""

import psycopg
from psycopg.types.json import Json

from sns.notify.dispatch import InMemoryAlertSink
from sns.notify.manual import (
    InMemoryAssignmentSource,
    ManualAssignment,
    PgAssignmentSource,
    notify_pending_assignments,
)

# ── InMemory: notify_pending_assignments 순수 로직 ───────────────────

_A = ManualAssignment(
    event_id="ev-1",
    cycle_id="cy-1",
    channel_id="ch-1",
    platform="instagram",
    topic_title="가을 산책",
)


def test_notify_dispatches_and_marks_notified_on_record_success() -> None:
    source = InMemoryAssignmentSource((_A,))
    sink = InMemoryAlertSink()

    results = notify_pending_assignments(source, sink=sink)

    assert len(results) == 1 and results[0].recorded is True
    assert source.notified == ["ev-1"]
    assert source.list_unnotified() == ()  # 통지 완료 → 대기 목록에서 사라짐
    assert sink.kinds() == ["manual_assigned"]


def test_notify_does_not_mark_notified_when_record_fails() -> None:
    class _FailingSink:
        def record(self, alert: object) -> None:
            raise RuntimeError("db down")

    source = InMemoryAssignmentSource((_A,))
    results = notify_pending_assignments(source, sink=_FailingSink())  # type: ignore[arg-type]

    assert results[0].recorded is False
    assert source.notified == []
    assert source.list_unnotified() == (_A,)  # 재시도 대상으로 남는다


def test_notify_empty_source_is_noop() -> None:
    results = notify_pending_assignments(InMemoryAssignmentSource(), sink=InMemoryAlertSink())
    assert results == []


# ── Pg: 실 SQL 멱등 검증 ──────────────────────────────────────────────


def _seed_assignment_event(db: psycopg.Connection, *, topic_title: str = "가을 산책") -> str:
    ch = db.execute(
        "INSERT INTO channel (platform, handle, mode) VALUES ('instagram', %s, 'manual') "
        "RETURNING id",
        (f"h-manual-{topic_title}",),
    ).fetchone()
    assert ch is not None
    cy = db.execute("INSERT INTO cycle (goal_ref) VALUES ('g') RETURNING id").fetchone()
    assert cy is not None
    row = db.execute(
        "INSERT INTO run_event (cycle_id, kind, payload) VALUES (%s, 'notice', %s) RETURNING id",
        (
            cy[0],
            Json(
                {
                    "reason": "manual_assignment",
                    "channel_id": str(ch[0]),
                    "topic_id": "topic-x",
                    "topic_title": topic_title,
                }
            ),
        ),
    ).fetchone()
    assert row is not None
    return str(row[0])


def test_pg_list_unnotified_finds_seeded_assignment(db: psycopg.Connection) -> None:
    event_id = _seed_assignment_event(db)
    source = PgAssignmentSource(db)
    assignments = source.list_unnotified()
    assert [a.event_id for a in assignments] == [event_id]
    assert assignments[0].platform == "instagram"
    assert assignments[0].topic_title == "가을 산책"


def test_pg_mark_notified_excludes_from_next_listing(db: psycopg.Connection) -> None:
    event_id = _seed_assignment_event(db)
    source = PgAssignmentSource(db)
    source.mark_notified(event_id)
    assert source.list_unnotified() == ()


def test_pg_notify_pending_assignments_is_idempotent_across_polls(db: psycopg.Connection) -> None:
    from sns.notify.dispatch import PgAlertSink

    _seed_assignment_event(db)
    source = PgAssignmentSource(db)
    sink = PgAlertSink(db)

    first = notify_pending_assignments(source, sink=sink)
    assert len(first) == 1 and first[0].recorded is True

    second = notify_pending_assignments(source, sink=sink)
    assert second == []  # 이미 통지됨 — 재통지 없음

    # run_event 3건: 원본 배정(reason) → 알림 적재(alert_kind) → 통지 완료 표식(reason).
    labels = [
        r[0]
        for r in db.execute(
            "SELECT COALESCE(payload ->> 'reason', payload ->> 'alert_kind') "
            "FROM run_event ORDER BY created_at"
        )
    ]
    assert labels == ["manual_assignment", "manual_assigned", "manual_assignment_notified"]
