"""수동(manual) 배정 알림 폴러 (C6+FR-E5 접점).

[sns.runner.cycle]은 manual 대상마다 `run_event(kind='notice', reason='manual_assignment')`
만 남기고 알림을 발신하지 않는다 — FR-W4가 명시한 발신 지점(발행 실패·사이클 최상위
catch)에 manual 배정은 없다. 이 모듈이 그 갭을 메운다: 아직 통지 안 된 배정을 찾아
[sns.notify.dispatch]로 흘려보내고, 통지 완료 표식을 같은 append-only `run_event`에
남겨 재실행해도 중복 알림이 안 나가게 한다(멱등).

DB 스키마 변경 없이 `run_event`만으로 멱등을 구현한다: 원본 이벤트 id를
`source_event_id`로 표식 이벤트 payload에 남기고, `NOT EXISTS`로 미통지 건만 고른다.
"""

from dataclasses import dataclass
from typing import Protocol

import psycopg
from psycopg.types.json import Json

from sns.notify.alerts import manual_assigned
from sns.notify.discord import WebhookSender
from sns.notify.dispatch import AlertSink, DispatchResult, dispatch_alert
from sns.tools.contracts import Platform


@dataclass(frozen=True)
class ManualAssignment:
    event_id: str
    cycle_id: str
    channel_id: str
    platform: Platform
    topic_title: str


class AssignmentSource(Protocol):
    """미통지 manual 배정 조회 + 통지 완료 표식 seam."""

    def list_unnotified(self) -> tuple[ManualAssignment, ...]: ...
    def mark_notified(self, event_id: str) -> None: ...


class InMemoryAssignmentSource:
    """결정론 테스트용 인메모리 구현."""

    def __init__(self, assignments: tuple[ManualAssignment, ...] = ()) -> None:
        self._pending: dict[str, ManualAssignment] = {a.event_id: a for a in assignments}
        self.notified: list[str] = []

    def list_unnotified(self) -> tuple[ManualAssignment, ...]:
        return tuple(self._pending.values())

    def mark_notified(self, event_id: str) -> None:
        self._pending.pop(event_id, None)
        self.notified.append(event_id)


_SELECT_UNNOTIFIED = """
SELECT re.id, re.cycle_id, re.payload ->> 'channel_id', ch.platform,
       COALESCE(re.payload ->> 'topic_title', '')
  FROM run_event re
  JOIN channel ch ON ch.id = (re.payload ->> 'channel_id')::uuid
 WHERE re.kind = 'notice'
   AND re.payload ->> 'reason' = 'manual_assignment'
   AND NOT EXISTS (
       SELECT 1 FROM run_event n
        WHERE n.kind = 'notice'
          AND n.payload ->> 'reason' = 'manual_assignment_notified'
          AND n.payload ->> 'source_event_id' = re.id::text
   )
 ORDER BY re.created_at
"""


class PgAssignmentSource:
    """psycopg 백엔드. autocommit 커넥션을 주입받는다([sns.publish.stores] 규율과 동일)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def list_unnotified(self) -> tuple[ManualAssignment, ...]:
        rows = self._conn.execute(_SELECT_UNNOTIFIED).fetchall()
        return tuple(
            ManualAssignment(
                event_id=str(event_id),
                cycle_id=str(cycle_id),
                channel_id=str(channel_id),
                platform=platform,
                topic_title=topic_title,
            )
            for event_id, cycle_id, channel_id, platform, topic_title in rows
        )

    def mark_notified(self, event_id: str) -> None:
        row = self._conn.execute(
            "SELECT cycle_id FROM run_event WHERE id = %s", (event_id,)
        ).fetchone()
        cycle_id = row[0] if row is not None else None
        self._conn.execute(
            "INSERT INTO run_event (cycle_id, kind, payload) VALUES (%s, 'notice', %s)",
            (
                cycle_id,
                Json({"reason": "manual_assignment_notified", "source_event_id": event_id}),
            ),
        )


def notify_pending_assignments(
    source: AssignmentSource, *, sink: AlertSink, sender: WebhookSender | None = None
) -> list[DispatchResult]:
    """미통지 배정을 전부 통지하고 완료 표식을 남긴다.

    DB 적재(`recorded`)가 source of truth라 그것만 성공하면 표식을 남긴다 — Discord
    전송(`delivered`)이 실패해도 재통지 스팸을 만들지 않는다(dispatch_alert와 같은 규율,
    [sns.notify.dispatch] 참조). 적재까지 실패하면 다음 폴링에서 재시도되도록 표식을
    남기지 않는다.
    """
    results: list[DispatchResult] = []
    for assignment in source.list_unnotified():
        alert = manual_assigned(
            assignment.platform,
            channel_id=assignment.channel_id,
            topic_title=assignment.topic_title,
            cycle_id=assignment.cycle_id,
        )
        result = dispatch_alert(alert, sink=sink, sender=sender)
        if result.recorded:
            source.mark_notified(assignment.event_id)
        results.append(result)
    return results


# mypy(sns): 두 구현이 동결 계약 AssignmentSource를 구조적으로 만족함을 강제.
_check_inmemory: AssignmentSource = InMemoryAssignmentSource()


def _check_pg(store: PgAssignmentSource) -> AssignmentSource:
    return store
