"""수동(manual) 배정 알림 CLI (FR-E5).

사이클이 남긴 `manual_assignment` notice 중 아직 통지 안 된 건을 찾아 Discord로
알린다. 사이클 오케스트레이터 자체는 알림을 발신하지 않으므로([sns.notify.manual]
참조), `run_cycle` 직후 이어서 부르거나 별도 주기(크론)로 돌린다. 여러 번 실행해도
같은 배정을 두 번 통지하지 않는다(멱등).

실행: uv run python scripts/manual_notify_assignments.py
전제: docker compose up -d postgres. DISCORD_WEBHOOK_URL 없으면 DB 적재만 한다.
"""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from sns.notify.discord import discord_sender_from_env
from sns.notify.dispatch import PgAlertSink
from sns.notify.manual import PgAssignmentSource, notify_pending_assignments

ENV_FILE = Path(__file__).parent.parent / ".env"
DEFAULT_DSN = "postgresql://sns:sns@localhost:5432/sns"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    load_dotenv(ENV_FILE, override=False)
    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    try:
        conn = psycopg.connect(dsn, connect_timeout=10, autocommit=True)
    except psycopg.OperationalError as exc:
        print(f"중단: PostgreSQL 연결 실패 — docker compose up -d postgres\n      {exc}")
        return 1

    sender = discord_sender_from_env()
    if sender is None:
        print("DISCORD_WEBHOOK_URL 없음 — DB 적재만(전송 생략)")

    results = notify_pending_assignments(
        PgAssignmentSource(conn), sink=PgAlertSink(conn), sender=sender
    )
    recorded = sum(r.recorded for r in results)
    delivered = sum(r.delivered for r in results)
    print(f"통지 대상 {len(results)}건 — DB적재={recorded} Discord전송={delivered}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
