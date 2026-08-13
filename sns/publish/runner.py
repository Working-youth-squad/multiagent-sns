"""발행 러너 — 품질 게이트 배선 + 멱등 상태머신 구동 (C5, 07-발행 §2).

DB에서 발행 대기(`publication.status='pending'`) 건을 읽어, 렌더된 자산의 품질
게이트 결과(`media_asset.quality_status`)를 상태머신의 `quality_passed`로 **배선**한다:

- `passed`  → `run_publish`로 종결 상태까지 1회 전진 (멱등 — 재구동해도 이중 발행 0).
- 그 외    → `publication`을 `skipped`로 종결 (05 FR-Q: 게이트는 진입만 막는다).
- 자산 없음 → `pending` 유지(다음 렌더 사이클 대기) + notice 기록.

부작용(외부 발행)은 주입된 `publish`(동결 `Publish` 계약)로만 — 프레임워크·벤더
무관이라 `FakePublish`로 결정론 테스트 가능. 실 `Publish` 디스패처(IG/YT 어댑터 +
MediaStore 바이트 조회) 배선은 호출자 몫이다.

커넥션은 **autocommit**을 가정한다([sns.publish.stores] docstring 참조).
"""

from dataclasses import dataclass
from typing import Literal

import psycopg
from psycopg.types.json import Json

from sns.publish.state_machine import PublishAttempt, run_publish
from sns.publish.stores import PgPublishAttemptStore
from sns.tools.contracts import MediaAsset, Publish

Outcome = Literal["published", "failed", "retryable", "skipped", "no_media"]

# 대기 발행 건 + 매칭 자산 조인. 발행할 kind는 content_item.format에서 파생
# (피드=이미지, 릴스/쇼츠=영상). 자산이 없으면 ma.*는 NULL.
_SELECT_PENDING = """
SELECT DISTINCT ON (p.id)
       p.id, ci.cycle_id, ch.platform, COALESCE(ci.body, ''),
       ma.kind, ma.storage_url, ma.checksum, ma.quality_status
  FROM publication p
  JOIN channel ch      ON ch.id = p.channel_id
  JOIN content_item ci ON ci.id = p.content_item_id
  LEFT JOIN media_asset ma
    ON ma.content_item_id = ci.id
   AND ma.kind = CASE WHEN ci.format = 'feed_image' THEN 'image' ELSE 'video' END
 WHERE p.status = 'pending'
 ORDER BY p.id, ma.created_at DESC
"""


@dataclass(frozen=True)
class RunnerResult:
    publication_id: str
    outcome: Outcome
    attempt: PublishAttempt | None  # skipped·no_media는 상태머신 미진입 → None


def _log_event(
    conn: psycopg.Connection, cycle_id: object, kind: str, payload: dict[str, object]
) -> None:
    conn.execute(
        "INSERT INTO run_event (cycle_id, kind, payload) VALUES (%s, %s, %s)",
        (cycle_id, kind, Json(payload)),
    )


def run_pending_publications(conn: psycopg.Connection, publish: Publish) -> list[RunnerResult]:
    """대기 발행 건을 각각 종결(published/failed/skipped)까지 1회 전진시킨다.

    한 건의 실패는 다른 건에 영향을 주지 않는다(채널 격리, FR-P4). 멱등: 다시
    호출해도 이미 종결된 publication은 재선택되지 않고, 진행 중(container_created)
    건만 이어서 재시도한다.
    """
    rows = conn.execute(_SELECT_PENDING).fetchall()  # autocommit: 열린 tx 없음
    store = PgPublishAttemptStore(conn)
    results: list[RunnerResult] = []

    for pub_id, cycle_id, platform, caption, kind, storage_url, checksum, qstatus in rows:
        publication_id = str(pub_id)

        if kind is None:
            _log_event(
                conn, cycle_id, "notice", {"publication_id": publication_id, "reason": "no_media"}
            )
            results.append(RunnerResult(publication_id, "no_media", None))
            continue

        # 게이트 배선: passed가 아니면 발행 진입 자체를 막고 skipped로 종결.
        if qstatus != "passed":
            with conn.transaction():
                conn.execute("UPDATE publication SET status = 'skipped' WHERE id = %s", (pub_id,))
                _log_event(
                    conn,
                    cycle_id,
                    "notice",
                    {
                        "publication_id": publication_id,
                        "reason": "quality_gate",
                        "quality_status": qstatus,
                    },
                )
            results.append(RunnerResult(publication_id, "skipped", None))
            continue

        media = MediaAsset(kind=kind, storage_url=storage_url, checksum=checksum)
        attempt = run_publish(
            store=store,
            publish=publish,
            publication_id=publication_id,
            platform=platform,
            media=media,
            caption=caption,
            idempotency_key=publication_id,  # publication당 안정 키 → 재구동 멱등
            quality_passed=True,
        )
        outcome: Outcome = (
            "published"
            if attempt.state == "published"
            else "failed"
            if attempt.state == "failed"
            else "retryable"
        )
        _log_event(
            conn,
            cycle_id,
            "publish_attempted",
            {
                "publication_id": publication_id,
                "state": attempt.state,
                "error_class": attempt.error_class,
                "external_post_id": attempt.external_post_id,
            },
        )
        results.append(RunnerResult(publication_id, outcome, attempt))

    return results
