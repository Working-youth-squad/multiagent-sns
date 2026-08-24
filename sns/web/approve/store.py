"""hybrid 승인 화면 데이터 계층 (C9, FR-Q3·FR-E3의 UI 접점, 10-웹-알림 §3).

주제 선택은 사이클이 이미 끝냈다(사이클당 주제 1건, 01 통제변수) — 여기 몫은
'초안 확인 → 수정 → 승인/반려'뿐이다.

승인/반려는 [sns.publish.runner]의 실제 발행 게이트를 직접 뒤집는다:
- **승인**: `content_item.status`를 `approved`로, 함께 존재하는 `media_asset`의
  `quality_status`를 `passed`로 올린다. 수정된 본문이면 `edited_by_human=true`
  (FR-E3, auto vs hybrid 비교의 축). 이 두 컬럼이 러너의 hybrid 콘텐츠 승인 관문
  전체다 — 승인 즉시 다음 러너 구동에서 발행된다.
- **반려**: `content_item.status='rejected'` + `media_asset.quality_status='failed'`
  + `publication.status='skipped'`(러너 재선택 방지) — 미디어 품질과 무관하게 즉시
  종결한다.

대상 범위(`list_pending`): `COALESCE(publication.mode, channel.mode)='hybrid'` +
`publication.status='pending'` + (`content_item.status != 'approved'` 이거나
`media_asset.quality_status = 'needs_review'`) — 러너가 실제로 막는 조건과 동형이라
"승인 대기 목록에 없다 = 이미 발행 진입 가능"이 성립한다.
"""

from dataclasses import dataclass
from typing import Protocol

import psycopg
from psycopg.types.json import Json


class ApprovalNotFound(LookupError):
    """대상 건이 없거나 이미 처리됨(이중 승인/반려, 대상 이탈 방어)."""


@dataclass(frozen=True)
class PendingItem:
    content_item_id: str
    cycle_id: str
    topic_title: str
    content_format: str
    hook_pattern: str | None
    body: str
    media_asset_id: str | None
    media_kind: str | None
    media_storage_url: str | None
    quality_status: str | None
    publication_id: str
    channel_id: str
    platform: str
    handle: str


class ApprovalStore(Protocol):
    """승인 화면 앱이 의존하는 유일한 영속화 계약."""

    def list_pending(self) -> tuple[PendingItem, ...]: ...
    def get_pending(self, content_item_id: str) -> PendingItem | None: ...
    def approve(self, content_item_id: str, *, body: str) -> None: ...
    def reject(self, content_item_id: str, *, reason: str) -> None: ...


class InMemoryApprovalStore:
    """결정론 테스트용 인메모리 구현. 승인/반려 이력을 그대로 노출해 앱 테스트가 검증."""

    def __init__(self, items: tuple[PendingItem, ...] = ()) -> None:
        self._items: dict[str, PendingItem] = {i.content_item_id: i for i in items}
        self.approved: dict[str, str] = {}  # content_item_id -> 승인된 본문
        self.rejected: dict[str, str] = {}  # content_item_id -> 반려 사유

    def list_pending(self) -> tuple[PendingItem, ...]:
        return tuple(self._items.values())

    def get_pending(self, content_item_id: str) -> PendingItem | None:
        return self._items.get(content_item_id)

    def approve(self, content_item_id: str, *, body: str) -> None:
        if content_item_id not in self._items:
            raise ApprovalNotFound(content_item_id)
        self.approved[content_item_id] = body
        del self._items[content_item_id]

    def reject(self, content_item_id: str, *, reason: str) -> None:
        if content_item_id not in self._items:
            raise ApprovalNotFound(content_item_id)
        self.rejected[content_item_id] = reason
        del self._items[content_item_id]


_SELECT_PENDING = """
SELECT ci.id, ci.cycle_id, t.title, ci.format, ci.hook_pattern, COALESCE(ci.body, ''),
       ma.id, ma.kind, ma.storage_url, ma.quality_status,
       p.id, p.channel_id, ch.platform, ch.handle
  FROM content_item ci
  JOIN publication p ON p.content_item_id = ci.id
  JOIN channel ch ON ch.id = p.channel_id
  JOIN topic t ON t.id = ci.topic_id
  LEFT JOIN media_asset ma ON ma.content_item_id = ci.id
 WHERE p.status = 'pending'
   AND COALESCE(p.mode, ch.mode) = 'hybrid'
   AND (ci.status != 'approved' OR ma.quality_status = 'needs_review')
"""


class PgApprovalStore:
    """psycopg 백엔드. autocommit 커넥션을 주입받는다([sns.publish.stores] 규율과 동일)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def list_pending(self) -> tuple[PendingItem, ...]:
        rows = self._conn.execute(f"{_SELECT_PENDING} ORDER BY ci.created_at").fetchall()
        return tuple(_row_to_item(r) for r in rows)

    def get_pending(self, content_item_id: str) -> PendingItem | None:
        row = self._conn.execute(f"{_SELECT_PENDING} AND ci.id = %s", (content_item_id,)).fetchone()
        return None if row is None else _row_to_item(row)

    def approve(self, content_item_id: str, *, body: str) -> None:
        item = self.get_pending(content_item_id)
        if item is None:
            raise ApprovalNotFound(content_item_id)
        edited = body != item.body
        with self._conn.transaction():
            self._conn.execute(
                "UPDATE content_item SET status = 'approved', body = %s, "
                "edited_by_human = edited_by_human OR %s WHERE id = %s",
                (body, edited, content_item_id),
            )
            if item.media_asset_id is not None:
                self._conn.execute(
                    "UPDATE media_asset SET quality_status = 'passed' WHERE id = %s",
                    (item.media_asset_id,),
                )
            self._log(item, "hybrid_approved", {"edited_by_human": edited})

    def reject(self, content_item_id: str, *, reason: str) -> None:
        item = self.get_pending(content_item_id)
        if item is None:
            raise ApprovalNotFound(content_item_id)
        with self._conn.transaction():
            self._conn.execute(
                "UPDATE content_item SET status = 'rejected' WHERE id = %s", (content_item_id,)
            )
            if item.media_asset_id is not None:
                self._conn.execute(
                    "UPDATE media_asset SET quality_status = 'failed' WHERE id = %s",
                    (item.media_asset_id,),
                )
            # 이미 종결된 publication(경쟁 상황)까지 뒤집지 않는다 — pending일 때만.
            self._conn.execute(
                "UPDATE publication SET status = 'skipped' WHERE id = %s AND status = 'pending'",
                (item.publication_id,),
            )
            self._log(item, "hybrid_rejected", {"reason": reason})

    def _log(self, item: PendingItem, reason: str, extra: dict[str, object]) -> None:
        self._conn.execute(
            "INSERT INTO run_event (cycle_id, kind, payload) VALUES (%s, 'notice', %s)",
            (
                item.cycle_id,
                Json({"reason": reason, "content_item_id": item.content_item_id, **extra}),
            ),
        )


def _row_to_item(row: tuple[object, ...]) -> PendingItem:
    (
        content_item_id,
        cycle_id,
        topic_title,
        content_format,
        hook_pattern,
        body,
        media_asset_id,
        media_kind,
        media_storage_url,
        quality_status,
        publication_id,
        channel_id,
        platform,
        handle,
    ) = row
    return PendingItem(
        content_item_id=str(content_item_id),
        cycle_id=str(cycle_id),
        topic_title=str(topic_title),
        content_format=str(content_format),
        hook_pattern=None if hook_pattern is None else str(hook_pattern),
        body=str(body),
        media_asset_id=None if media_asset_id is None else str(media_asset_id),
        media_kind=None if media_kind is None else str(media_kind),
        media_storage_url=None if media_storage_url is None else str(media_storage_url),
        quality_status=None if quality_status is None else str(quality_status),
        publication_id=str(publication_id),
        channel_id=str(channel_id),
        platform=str(platform),
        handle=str(handle),
    )


# mypy(sns): 두 구현이 동결 계약 ApprovalStore를 구조적으로 만족함을 강제.
_check_inmemory: ApprovalStore = InMemoryApprovalStore()


def _check_pg(store: PgApprovalStore) -> ApprovalStore:
    return store
