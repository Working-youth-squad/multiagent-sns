"""사이클 영속화 seam — CycleStore 계약 + InMemory/Pg 구현.

`PublishAttemptStore`(sns.publish.stores)와 같은 규율: 오케스트레이터(cycle.run_cycle)는
이 계약에만 의존해 순수 로직을 InMemory로 결정론 테스트하고, 운영 SQL은 PgCycleStore로
분리한다. 각 save는 독립 커밋 단위 — 사이클 도중 한 대상이 실패해도 앞서 확정된 행은
남고(부분 진행 관측 가능), append-only run_event로 흐름을 재구성한다.

PgCycleStore는 **autocommit 커넥션**을 가정한다(sns.publish.stores 규율과 동일):
media_asset·publication은 content_item FK에 의존하므로 러너가 순서대로 호출한다.
"""

from collections.abc import Mapping
from typing import Literal, Protocol

import psycopg
from psycopg.types.json import Json

from sns.tools.contracts import ContentFormat, MediaKind

# run_event.kind CHECK 부분집합 — 러너가 쓰는 값만.
# 001_initial.sql의 run_event.kind CHECK 부분집합. "notice"는 시스템 오류가 아닌 관측
# 사항이다 — 사진이 안 붙은 이유처럼, 사이클은 성공했지만 남겨야 할 사실.
EventKind = Literal[
    "cycle_started", "agent_called", "tool_called", "notice", "error", "cycle_completed"
]
CycleStatus = Literal["completed", "failed"]


class CycleStore(Protocol):
    """러너가 의존하는 유일한 영속화 계약. 모든 id는 문자열(UUID/합성)."""

    def create_cycle(self, goal_ref: str) -> str: ...
    def save_topic(self, *, title: str, summary: str, source: str) -> str: ...
    def recent_topic_titles(self, *, days: int) -> tuple[str, ...]: ...
    def recent_media_specs(self, *, days: int, limit: int) -> tuple[Mapping[str, object], ...]: ...
    # 되읽기 — 쓰기만 있는 저장소는 반쪽이다. 발행 스크립트·승인 화면이 산출물을
    # 확인하려면 필요하고, 없으면 호출부가 인메모리 내부 dict를 직접 뒤지게 된다.
    def read_content_item(self, content_item_id: str) -> Mapping[str, object]: ...
    def read_media_asset(self, media_asset_id: str) -> Mapping[str, object]: ...
    def read_topic(self, topic_id: str) -> Mapping[str, object]: ...
    def save_content_item(
        self,
        *,
        cycle_id: str,
        topic_id: str,
        content_format: ContentFormat,
        body: str,
        media_spec: Mapping[str, object],
        hook_pattern: str,
        status: str,
    ) -> str: ...
    def save_media_asset(
        self,
        *,
        content_item_id: str,
        kind: MediaKind,
        storage_url: str,
        checksum: str,
        quality_status: str,
        quality_report: Mapping[str, object] | None,
    ) -> str: ...
    def create_publication(self, *, content_item_id: str, channel_id: str) -> str: ...
    def complete_cycle(self, cycle_id: str, *, status: CycleStatus) -> None: ...
    def log_event(
        self, *, cycle_id: str, kind: EventKind, payload: Mapping[str, object]
    ) -> None: ...


class InMemoryCycleStore:
    """결정론 테스트·드라이런용 인메모리 원장. id는 접두사+증가 카운터."""

    def __init__(self) -> None:
        self.cycles: dict[str, dict[str, object]] = {}
        self.topics: dict[str, dict[str, object]] = {}
        self.content_items: dict[str, dict[str, object]] = {}
        self.media_assets: dict[str, dict[str, object]] = {}
        self.publications: dict[str, dict[str, object]] = {}
        self.events: list[dict[str, object]] = []
        self._seq = 0

    def _id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    def create_cycle(self, goal_ref: str) -> str:
        cid = self._id("cycle")
        self.cycles[cid] = {"goal_ref": goal_ref, "status": "running"}
        return cid

    def save_topic(self, *, title: str, summary: str, source: str) -> str:
        tid = self._id("topic")
        self.topics[tid] = {"title": title, "summary": summary, "source": source, "status": "used"}
        return tid

    def recent_topic_titles(self, *, days: int) -> tuple[str, ...]:
        """인메모리는 프로세스와 함께 사라지므로 days를 무시하고 전부 돌려준다."""
        return tuple(str(t["title"]) for t in self.topics.values())

    def recent_media_specs(self, *, days: int, limit: int) -> tuple[Mapping[str, object], ...]:
        specs = [ci["media_spec"] for ci in self.content_items.values()]
        return tuple(s for s in reversed(specs) if isinstance(s, Mapping))[:limit]

    def read_content_item(self, content_item_id: str) -> Mapping[str, object]:
        return self.content_items[content_item_id]

    def read_media_asset(self, media_asset_id: str) -> Mapping[str, object]:
        return self.media_assets[media_asset_id]

    def read_topic(self, topic_id: str) -> Mapping[str, object]:
        return self.topics[topic_id]

    def save_content_item(
        self,
        *,
        cycle_id: str,
        topic_id: str,
        content_format: ContentFormat,
        body: str,
        media_spec: Mapping[str, object],
        hook_pattern: str,
        status: str,
    ) -> str:
        ciid = self._id("content")
        self.content_items[ciid] = {
            "cycle_id": cycle_id,
            "topic_id": topic_id,
            "format": content_format,
            "body": body,
            "media_spec": dict(media_spec),
            "hook_pattern": hook_pattern,
            "status": status,
        }
        return ciid

    def save_media_asset(
        self,
        *,
        content_item_id: str,
        kind: MediaKind,
        storage_url: str,
        checksum: str,
        quality_status: str,
        quality_report: Mapping[str, object] | None,
    ) -> str:
        maid = self._id("media")
        self.media_assets[maid] = {
            "content_item_id": content_item_id,
            "kind": kind,
            "storage_url": storage_url,
            "checksum": checksum,
            "quality_status": quality_status,
            "quality_report": None if quality_report is None else dict(quality_report),
        }
        return maid

    def create_publication(self, *, content_item_id: str, channel_id: str) -> str:
        pid = self._id("pub")
        self.publications[pid] = {
            "content_item_id": content_item_id,
            "channel_id": channel_id,
            "status": "pending",
        }
        return pid

    def complete_cycle(self, cycle_id: str, *, status: CycleStatus) -> None:
        self.cycles[cycle_id]["status"] = status

    def log_event(self, *, cycle_id: str, kind: EventKind, payload: Mapping[str, object]) -> None:
        self.events.append({"cycle_id": cycle_id, "kind": kind, "payload": dict(payload)})


class PgCycleStore:
    """psycopg 백엔드. autocommit 커넥션을 주입받는다(모듈 docstring 참조)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def _scalar(self, sql: str, params: tuple[object, ...]) -> str:
        row = self._conn.execute(sql, params).fetchone()
        assert row is not None
        return str(row[0])

    def create_cycle(self, goal_ref: str) -> str:
        return self._scalar(
            "INSERT INTO cycle (goal_ref, status, started_at) "
            "VALUES (%s, 'running', now()) RETURNING id",
            (goal_ref,),
        )

    def save_topic(self, *, title: str, summary: str, source: str) -> str:
        return self._scalar(
            "INSERT INTO topic (title, summary, source, status) "
            "VALUES (%s, %s, %s, 'used') RETURNING id",
            (title, summary, source),
        )

    def recent_topic_titles(self, *, days: int) -> tuple[str, ...]:
        """최근 N일 안에 실제로 쓴 주제 제목 — 주제 중복 차단의 재료."""
        rows = self._conn.execute(
            "SELECT title FROM topic WHERE status = 'used' "
            "AND created_at > now() - make_interval(days => %s) ORDER BY created_at DESC",
            (days,),
        ).fetchall()
        return tuple(str(r[0]) for r in rows)

    def recent_media_specs(self, *, days: int, limit: int) -> tuple[Mapping[str, object], ...]:
        """최근 N일 콘텐츠의 media_spec — 근접중복 판정의 재료."""
        rows = self._conn.execute(
            "SELECT media_spec FROM content_item "
            "WHERE created_at > now() - make_interval(days => %s) AND media_spec IS NOT NULL "
            "ORDER BY created_at DESC LIMIT %s",
            (days, limit),
        ).fetchall()
        return tuple(r[0] for r in rows if isinstance(r[0], Mapping))

    def _row(
        self, sql: str, params: tuple[object, ...], fields: tuple[str, ...]
    ) -> dict[str, object]:
        row = self._conn.execute(sql, params).fetchone()
        if row is None:
            raise KeyError(params[0])
        return dict(zip(fields, row, strict=True))

    def read_content_item(self, content_item_id: str) -> Mapping[str, object]:
        return self._row(
            "SELECT body, hook_pattern, media_spec, status, format FROM content_item WHERE id = %s",
            (content_item_id,),
            ("body", "hook_pattern", "media_spec", "status", "format"),
        )

    def read_media_asset(self, media_asset_id: str) -> Mapping[str, object]:
        return self._row(
            "SELECT kind, storage_url, checksum, quality_status FROM media_asset WHERE id = %s",
            (media_asset_id,),
            ("kind", "storage_url", "checksum", "quality_status"),
        )

    def read_topic(self, topic_id: str) -> Mapping[str, object]:
        return self._row(
            "SELECT title, summary, source, status FROM topic WHERE id = %s",
            (topic_id,),
            ("title", "summary", "source", "status"),
        )

    def save_content_item(
        self,
        *,
        cycle_id: str,
        topic_id: str,
        content_format: ContentFormat,
        body: str,
        media_spec: Mapping[str, object],
        hook_pattern: str,
        status: str,
    ) -> str:
        return self._scalar(
            "INSERT INTO content_item "
            "(cycle_id, topic_id, format, status, hook_pattern, body, media_spec) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (cycle_id, topic_id, content_format, status, hook_pattern, body, Json(media_spec)),
        )

    def save_media_asset(
        self,
        *,
        content_item_id: str,
        kind: MediaKind,
        storage_url: str,
        checksum: str,
        quality_status: str,
        quality_report: Mapping[str, object] | None,
    ) -> str:
        return self._scalar(
            "INSERT INTO media_asset "
            "(content_item_id, kind, storage_url, checksum, quality_status, quality_report) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (
                content_item_id,
                kind,
                storage_url,
                checksum,
                quality_status,
                None if quality_report is None else Json(quality_report),
            ),
        )

    def create_publication(self, *, content_item_id: str, channel_id: str) -> str:
        return self._scalar(
            "INSERT INTO publication (content_item_id, channel_id) VALUES (%s, %s) RETURNING id",
            (content_item_id, channel_id),
        )

    def complete_cycle(self, cycle_id: str, *, status: CycleStatus) -> None:
        self._conn.execute(
            "UPDATE cycle SET status = %s, completed_at = now() WHERE id = %s",
            (status, cycle_id),
        )

    def log_event(self, *, cycle_id: str, kind: EventKind, payload: Mapping[str, object]) -> None:
        self._conn.execute(
            "INSERT INTO run_event (cycle_id, kind, payload) VALUES (%s, %s, %s)",
            (cycle_id, kind, Json(payload)),
        )


# mypy(sns): 두 구현이 동결 계약 CycleStore를 구조적으로 만족함을 강제.
_check_inmemory: CycleStore = InMemoryCycleStore()


def _check_pg(store: PgCycleStore) -> CycleStore:
    return store
