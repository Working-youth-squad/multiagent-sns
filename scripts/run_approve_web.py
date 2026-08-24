"""hybrid 승인 웹 서버 기동 (C9). uv run python scripts/run_approve_web.py

전제:
  1. docker compose up -d postgres
  2. env DATABASE_URL — (선택) 기본 postgresql://sns:sns@localhost:5432/sns
  3. env APPROVE_WEB_HOST/PORT — (선택) 기본 127.0.0.1:8001

단일 커넥션 전제(scripts/e2e_cycle.py와 동일 규율): 승인 화면은 저트래픽 내부
도구라 커넥션 풀 없이 앱 수명 동안 커넥션 1개를 autocommit으로 연다.
"""

import os
import sys
from pathlib import Path

import psycopg
import uvicorn
from dotenv import load_dotenv

from sns.web.approve.app import create_app
from sns.web.approve.store import PgApprovalStore

ENV_FILE = Path(__file__).parent.parent / ".env"
DEFAULT_DSN = "postgresql://sns:sns@localhost:5432/sns"


def main() -> int:
    load_dotenv(ENV_FILE, override=False)
    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    try:
        conn = psycopg.connect(dsn, connect_timeout=10, autocommit=True)
    except psycopg.OperationalError as exc:
        print(f"중단: PostgreSQL 연결 실패 — docker compose up -d postgres\n      {exc}")
        return 1

    app = create_app(PgApprovalStore(conn))
    host = os.environ.get("APPROVE_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("APPROVE_WEB_PORT", "8001"))
    print(f"승인 화면: http://{host}:{port}/")
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
