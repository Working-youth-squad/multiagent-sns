"""발행 러너 — 품질 게이트 배선 + 멱등 상태머신 구동 (C5, 07-발행 §2).

**기계 발행 경계**: 이 러너는 `auto`·`hybrid` 발행만 다룬다([sns.publish.modes]).
`manual`(수동)은 사람이 플랫폼에서 직접 발행하고 [sns.publish.manual]로 등록만
하므로 여기의 선택 쿼리에 절대 잡히지 않는다.

DB에서 발행 대기(`publication.status='pending'`) 건을 읽어, 렌더된 자산의 품질
게이트 결과(`media_asset.quality_status`)를 상태머신의 `quality_passed`로 **배선**한다:

- `passed`        → `run_publish`로 종결까지 1회 전진 (멱등 — 재구동해도 이중 발행 0).
- `failed`        → (진입 시에만) `publication`을 `skipped`로 종결 — 자동 게이트 하드
  실패 = 콘텐츠 거부. 게이트는 '진입'만 막는다(05 FR-Q): 진행 중 시도는 재검사 안 함.
- `needs_review`  → `pending` 유지 + notice. hybrid 사람 관문(FR-Q3) 대기 — 사람이
  승인해 `passed`로 바뀌면 다음 구동에서 발행된다. 영구 skipped로 종결하지 않는다.
  (자산 기본 quality_status가 `needs_review`라, 아직 게이트 미판정 자산도 여기 해당.)
- 자산 없음        → `pending` 유지(다음 렌더 사이클 대기) + notice 기록.

부작용(외부 발행)은 주입된 `publish`(동결 `Publish` 계약)로만 — 프레임워크·벤더
무관이라 `FakePublish`로 결정론 테스트 가능. 실 `Publish` 디스패처(IG/YT 어댑터 +
MediaStore 바이트 조회) 배선은 호출자 몫이다.

커넥션은 **autocommit**을 가정한다([sns.publish.stores] docstring 참조).

**단일 워커 전제**: `_SELECT_PENDING`에 행 잠금이 없어, 러너를 동시에 둘 돌리면 같은
건을 둘 다 선택할 수 있다. 그 경우 이중 발행 방지는 안정적 `idempotency_key`
(=publication_id) 위의 어댑터 멱등성에 의존한다. 다중 워커가 필요하면
`SELECT ... FOR UPDATE SKIP LOCKED`를 더한다.
"""

from dataclasses import dataclass
from typing import Literal

import psycopg
from psycopg.types.json import Json

from sns.publish.state_machine import AttemptStatus, PublishAttempt, run_publish
from sns.publish.stores import PgPublishAttemptStore
from sns.tools.contracts import MediaAsset, Publish

Outcome = Literal["published", "failed", "retryable", "skipped", "awaiting_review", "no_media"]

# 상태머신 종결/진행 상태 → 러너 outcome. 진행 중(pending·container_created)=retryable.
_STATE_OUTCOME: dict[AttemptStatus, Outcome] = {
    "published": "published",
    "failed": "failed",
    "pending": "retryable",
    "container_created": "retryable",
}

# 대기 발행 건 + 매칭 자산 조인. 발행할 kind는 content_item.format에서 파생
# (피드=이미지, 릴스/쇼츠=영상). 자산이 없으면 ma.*는 NULL. pa.state는 기존 시도
# 진행 여부(게이트 재적용 금지 판단)로 쓴다 — publish_attempt는 publication과 1-1.
_SELECT_PENDING = """
SELECT DISTINCT ON (p.id)
       p.id, ci.cycle_id, ch.platform, COALESCE(ci.body, ''),
       ma.kind, ma.storage_url, ma.checksum, ma.quality_status,
       pa.state, COALESCE(p.mode, ch.mode)
  FROM publication p
  JOIN channel ch      ON ch.id = p.channel_id
  JOIN content_item ci ON ci.id = p.content_item_id
  LEFT JOIN media_asset ma
    ON ma.content_item_id = ci.id
   AND ma.kind = CASE WHEN ci.format = 'feed_image' THEN 'image' ELSE 'video' END
  LEFT JOIN publish_attempt pa ON pa.publication_id = p.id
 WHERE p.status = 'pending'
   -- 기계 발행은 auto·hybrid만(sns.publish.modes MACHINE_MODES와 동일 집합).
   -- manual은 사람이 직접 발행·등록(FR-E5), off는 발행 중지.
   AND COALESCE(p.mode, ch.mode) IN ('auto', 'hybrid')
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

    for row in rows:
        (
            pub_id,
            cycle_id,
            platform,
            caption,
            kind,
            storage_url,
            checksum,
            qstatus,
            attempt_state,
            mode,
        ) = row
        publication_id = str(pub_id)

        if kind is None:
            _log_event(
                conn, cycle_id, "notice", {"publication_id": publication_id, "reason": "no_media"}
            )
            results.append(RunnerResult(publication_id, "no_media", None))
            continue

        # 게이트 배선(진입 시에만 — 이미 진행 중인 시도는 위 attempt_state로 통과):
        # state_machine 불변식과 일치시켜 재검사 금지. 이 조건이 없으면 첫 판정 뒤
        # 새로 렌더된 저품질 자산이 최신으로 잡혀, 컨테이너를 남긴 채 publication만
        # 뒤집혀 원장↔발행상태가 갈라진다.
        if attempt_state is None and qstatus != "passed":
            # failed=자동 게이트 하드 실패(콘텐츠 거부)만 skipped로 종결한다.
            # needs_review는 사람 승인 대기라 pending 유지 — skipped로 종결하면
            # 나중에 승인돼도 재선택 안 돼 영영 발행되지 않는다(FR-Q3).
            if qstatus == "failed":
                with conn.transaction():
                    conn.execute(
                        "UPDATE publication SET status = 'skipped' WHERE id = %s", (pub_id,)
                    )
                    _log_event(
                        conn,
                        cycle_id,
                        "notice",
                        {"publication_id": publication_id, "reason": "quality_failed"},
                    )
                results.append(RunnerResult(publication_id, "skipped", None))
            else:
                _log_event(
                    conn,
                    cycle_id,
                    "notice",
                    {
                        "publication_id": publication_id,
                        "reason": "awaiting_review",
                        "quality_status": qstatus,
                    },
                )
                results.append(RunnerResult(publication_id, "awaiting_review", None))
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
            # 진입 시(attempt_state=None)만 게이트가 유효 — 진행 중이면 run_publish가 무시.
            quality_passed=qstatus == "passed",
        )
        outcome = _STATE_OUTCOME[attempt.state]
        _log_event(
            conn,
            cycle_id,
            "publish_attempted",
            {
                "publication_id": publication_id,
                "mode": mode,
                "state": attempt.state,
                "error_class": attempt.error_class,
                "external_post_id": attempt.external_post_id,
            },
        )
        results.append(RunnerResult(publication_id, outcome, attempt))

    return results
