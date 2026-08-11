"""번호순 .sql 마이그레이션 러너 (NFR-9: 전진 순번 · 트랜잭션 원자성).

실행: DATABASE_URL=... python -m sns.db.migrate
"""

import os
import re
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def apply_migrations(conn: psycopg.Connection) -> list[int]:
    """미적용 마이그레이션을 번호순으로 각각 독립 트랜잭션에서 적용·커밋한다.

    각 마이그레이션은 자체 top-level 트랜잭션으로 커밋되므로 호출자의 추가
    commit이 필요 없고, 도중 실패 시 그 마이그레이션만 롤백된다(앞선 것은 유지).
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " version integer PRIMARY KEY,"
        " applied_at timestamptz NOT NULL DEFAULT now())"
    )
    conn.commit()
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
    # 위 SELECT가 연 읽기 트랜잭션을 닫는다 — 그래야 아래 각 conn.transaction()이
    # 세이브포인트가 아니라 독립 top-level 트랜잭션으로 커밋된다.
    conn.rollback()
    done: list[int] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = re.match(r"(\d+)", path.name)
        if match is None:
            raise ValueError(f"마이그레이션 파일명은 번호로 시작해야 함: {path.name}")
        version = int(match.group(1))
        if version in applied:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_version (version) VALUES (%s)", (version,))
        done.append(version)
    return done


def main() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        done = apply_migrations(conn)
        print(f"applied: {done if done else 'up to date'}")


if __name__ == "__main__":
    main()
