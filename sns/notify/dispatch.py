"""알림 디스패치 (C6, FR-W4) — 원인분석 → Discord 전송 → run_event 이중적재.

발신 지점(Publisher 실패처리부·사이클 최상위 catch)이 부르는 단일 진입점.
불변식: **어떤 외부 장애도 호출자로 전파하지 않는다** — dispatch는 실패 처리 경로에서
불리므로, 여기서 던지면 원래 오류를 삼키거나 사이클을 두 번 죽인다.

- LLM 원인분석 실패 → 삼킴(분류명으로 폴백, cause_line=None 유지).
- Discord 전송 실패 → 삼킴(delivered=False). DB엔 이미 적재됐다.
- DB 적재 실패 → 삼킴(recorded=False). 최후의 방어 — 여기서도 던지지 않는다.

결과 플래그(`DispatchResult`)로 무엇이 됐는지 알린다. 적재는 source of truth라
전송보다 먼저 시도한다(전송이 죽어도 기록은 남게).

`PgAlertSink`는 [sns.publish.stores]와 같은 autocommit 커넥션을 가정한다.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

import psycopg
from langchain_core.language_models import BaseChatModel
from psycopg.types.json import Json

from sns.notify.alerts import Alert, event_kind, event_payload
from sns.notify.cause import analyze_cause
from sns.notify.discord import WebhookSender, discord_payload


class AlertSink(Protocol):
    """알림 DB 적재 seam. 구현이 예외를 던져도 dispatch가 삼킨다."""

    def record(self, alert: Alert) -> None: ...


class InMemoryAlertSink:
    """결정론 테스트·드라이런용 인메모리 적재."""

    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def record(self, alert: Alert) -> None:
        self.alerts.append(alert)

    def kinds(self) -> Sequence[str]:
        return [a.kind for a in self.alerts]


class PgAlertSink:
    """psycopg 백엔드 — 알림을 run_event(kind∈{error,notice})로 적재.

    cycle_id는 alert.context['cycle_id']가 있을 때만 채운다(run_event.cycle_id는
    nullable FK). 발행 단위 알림은 cycle 참조가 없어 NULL로 남는다.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def record(self, alert: Alert) -> None:
        cycle_id = alert.context.get("cycle_id")
        with self._conn.transaction():
            self._conn.execute(
                "INSERT INTO run_event (cycle_id, kind, payload) VALUES (%s, %s, %s)",
                (cycle_id, event_kind(alert), Json(event_payload(alert))),
            )


@dataclass(frozen=True)
class DispatchResult:
    recorded: bool  # DB 적재 성공 (목표: 항상 True)
    delivered: bool  # Discord 전송 성공
    cause_analyzed: bool  # LLM 원인 1줄 부착됨


def dispatch_alert(
    alert: Alert,
    *,
    sink: AlertSink,
    sender: WebhookSender | None = None,
    model: BaseChatModel | None = None,
) -> DispatchResult:
    """알림 1건을 처리한다. 외부 장애를 삼키고 결과를 플래그로 돌려준다."""
    # 1. 실패 알림이면 LLM 원인 1줄(장애·빈결과는 None → 분류명 폴백).
    cause_analyzed = False
    if model is not None and alert.severity == "error" and alert.cause_line is None:
        cause = analyze_cause(model, alert)
        if cause:
            alert = replace(alert, cause_line=cause)
            cause_analyzed = True

    # 2. DB 적재 — source of truth. 전송보다 먼저.
    recorded = False
    try:
        sink.record(alert)
        recorded = True
    except Exception:
        recorded = False

    # 3. Discord 전송 — best-effort.
    delivered = False
    if sender is not None:
        try:
            sender(discord_payload(alert))
            delivered = True
        except Exception:
            delivered = False

    return DispatchResult(recorded=recorded, delivered=delivered, cause_analyzed=cause_analyzed)
