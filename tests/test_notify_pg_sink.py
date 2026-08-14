"""PgAlertSink — run_event 이중적재를 라이브 스키마로 검증(FR-W4 수용기준: DB 기록).

conftest의 `db` 픽스처(autocommit·세션 스키마) 재사용. PostgreSQL 미가동 시 skip.
"""

import psycopg

from sns.notify.alerts import cycle_error, publish_failure
from sns.notify.dispatch import PgAlertSink, dispatch_alert
from sns.tools.contracts import ToolError


def _events(db: psycopg.Connection) -> list[tuple[str, dict, object]]:
    rows = db.execute(
        "SELECT kind, payload, cycle_id FROM run_event ORDER BY created_at"
    ).fetchall()
    return [(k, p, c) for k, p, c in rows]


def test_failure_alert_persists_error_event(db: psycopg.Connection) -> None:
    alert = publish_failure(
        "instagram", ToolError("spam_block", "action blocked"), publication_id="pub-x"
    )
    PgAlertSink(db).record(alert)

    events = _events(db)
    assert len(events) == 1
    kind, payload, cycle_id = events[0]
    assert kind == "error"  # severity=error → run_event.kind
    assert payload["alert_kind"] == "publish_failure"
    assert payload["error_class"] == "spam_block"
    assert payload["error_raw"] == "action blocked"
    assert payload["context"] == {"publication_id": "pub-x"}
    assert cycle_id is None  # 발행 단위 알림 — cycle 참조 없음


def test_cycle_error_links_cycle_id(db: psycopg.Connection) -> None:
    row = db.execute("INSERT INTO cycle (goal_ref) VALUES ('g') RETURNING id").fetchone()
    assert row is not None
    cycle_id = str(row[0])

    PgAlertSink(db).record(cycle_error(error_raw="boom", cycle_id=cycle_id))

    kind, payload, linked = _events(db)[0]
    assert kind == "error"
    assert str(linked) == cycle_id  # FK 연결 확인
    assert payload["error_raw"] == "boom"


def test_dispatch_end_to_end_delivers_and_records(db: psycopg.Connection) -> None:
    # 강제 실패 주입 → 웹훅 수신(가짜 sender) + DB 기록 (FR-W4 수용기준 그대로).
    sent: list[dict[str, object]] = []
    alert = publish_failure("youtube", ToolError("quota", "limit"), publication_id="pub-y")
    result = dispatch_alert(alert, sink=PgAlertSink(db), sender=sent.append)

    assert result.recorded and result.delivered
    assert len(sent) == 1
    assert len(_events(db)) == 1
