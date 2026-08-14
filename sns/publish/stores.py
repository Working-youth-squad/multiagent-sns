"""PublishAttemptStore 구현.

- `InMemoryPublishAttemptStore`: 결정론 테스트·러너 드라이런용 (NFR-2).
- `PgPublishAttemptStore`: 운영 psycopg 백엔드. `save`는 `publish_attempt`와
  `publication`을 **한 트랜잭션**에서 함께 갱신해 원장과 발행 상태가 절대
  어긋나지 않게 한다 (FR-P3: 이중 발행 0의 전제).

`PgPublishAttemptStore`는 **autocommit 커넥션**을 가정한다: `load`의 단일 SELECT는
즉시 커밋되어 열린 읽기 트랜잭션을 남기지 않고, `save`의 `conn.transaction()`은
독립 top-level 트랜잭션으로 원자 커밋된다. (비-autocommit이면 `save`의
`transaction()`이 앞선 SELECT가 연 트랜잭션의 세이브포인트로 중첩되어 커밋되지
않는다 — `sns.db.migrate`가 문서화한 함정과 동일.)
"""

import psycopg

from sns.publish.state_machine import PublishAttempt, PublishAttemptStore


class InMemoryPublishAttemptStore:
    """publication_id → PublishAttempt 인메모리 원장."""

    def __init__(self) -> None:
        self._data: dict[str, PublishAttempt] = {}

    def load(self, publication_id: str) -> PublishAttempt | None:
        return self._data.get(publication_id)

    def save(self, attempt: PublishAttempt) -> None:
        self._data[attempt.publication_id] = attempt


class PgPublishAttemptStore:
    """psycopg 백엔드 원장. autocommit 커넥션을 주입받는다(모듈 docstring 참조)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def load(self, publication_id: str) -> PublishAttempt | None:
        # external_post_id는 publication에 산다 — published일 때만 의미가 있다.
        row = self._conn.execute(
            """
            SELECT pa.state, pa.container_id, pa.error_class, pa.error_raw,
                   p.external_post_id
              FROM publish_attempt pa
              JOIN publication p ON p.id = pa.publication_id
             WHERE pa.publication_id = %s
            """,
            (publication_id,),
        ).fetchone()
        if row is None:
            return None
        state, container_id, error_class, error_raw, external_post_id = row
        return PublishAttempt(
            publication_id=publication_id,
            state=state,
            container_id=container_id,
            error_class=error_class,
            error_raw=error_raw,
            external_post_id=external_post_id if state == "published" else None,
        )

    def save(self, attempt: PublishAttempt) -> None:
        # publish_attempt(원장)와 publication(발행 상태)을 원자적으로 함께 갱신한다.
        # 둘이 갈라지면 재구동 시 상태머신의 종결 판정과 publication.status가
        # 어긋나 이중 발행/유령 실패가 생긴다.
        with self._conn.transaction():
            self._conn.execute(
                """
                INSERT INTO publish_attempt
                    (publication_id, state, container_id, error_class, error_raw)
                VALUES (%(pid)s, %(state)s, %(cid)s, %(ec)s, %(er)s)
                ON CONFLICT (publication_id) DO UPDATE SET
                    state        = EXCLUDED.state,
                    container_id = EXCLUDED.container_id,
                    error_class  = EXCLUDED.error_class,
                    error_raw    = EXCLUDED.error_raw,
                    updated_at   = now()
                """,
                {
                    "pid": attempt.publication_id,
                    "state": attempt.state,
                    "cid": attempt.container_id,
                    "ec": attempt.error_class,
                    "er": attempt.error_raw,
                },
            )
            if attempt.state == "published":
                self._conn.execute(
                    """
                    UPDATE publication
                       SET status = 'published',
                           external_post_id = %s,
                           published_at = now()
                     WHERE id = %s
                    """,
                    (attempt.external_post_id, attempt.publication_id),
                )
            elif attempt.state == "failed":
                self._conn.execute(
                    "UPDATE publication SET status = 'failed' WHERE id = %s",
                    (attempt.publication_id,),
                )
            # pending·container_created: publication.status는 'pending' 유지(재시도 여지).


# mypy(sns): 두 구현이 동결 계약 PublishAttemptStore를 구조적으로 만족함을 강제.
_check_inmemory: PublishAttemptStore = InMemoryPublishAttemptStore()


def _check_pg(store: PgPublishAttemptStore) -> PublishAttemptStore:
    return store
