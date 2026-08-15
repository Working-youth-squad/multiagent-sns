"""사이클 오케스트레이터 — 라이브 PostgreSQL 통합(스키마·FK·jsonb 적재 검증).

run_cycle(PgCycleStore) → run_pending_publications 합성이 **한 사이클 무인 관통**
(기획→제작→적재→발행)임을 실제 DB로 확인. PG 미가동 시 conftest가 skip.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import psycopg
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from sns.publish.runner import run_pending_publications
from sns.quality.gate import QualityReport
from sns.runner.cycle import CycleTarget, run_cycle
from sns.runner.store import PgCycleStore
from sns.tools.contracts import ContentFormat, MediaAsset
from sns.tools.fakes import FakePublish, FakeReadStats, FakeRenderMedia, FakeResearchTrends

_CARD_SPEC = {"hook": "3초컷", "title": "walrus", "body": ["a := 10"], "footer": "팔로우"}


class ScriptedChatModel(GenericFakeChatModel):
    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self


def _tool(name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "c1"}])


def _script() -> list[AIMessage]:
    return [
        _tool("choose_topic", {"index": 0, "category": "꿀팁", "summary": "요약"}),
        AIMessage(content="주제 확정"),
        _tool("set_hook", {"pattern": "curiosity"}),
        _tool("set_media_spec", {"spec_json": json.dumps(_CARD_SPEC, ensure_ascii=False)}),
        AIMessage(content="본문 #개발"),
    ]


def _passing(
    *, media_spec: Mapping[str, object], media: MediaAsset, content_format: ContentFormat
) -> QualityReport:
    return QualityReport(status="passed", checks=())


def _channel(db: psycopg.Connection) -> str:
    row = db.execute(
        "INSERT INTO channel (platform, handle, mode) "
        "VALUES ('instagram', 'h-cycle', 'auto') RETURNING id"
    ).fetchone()
    assert row is not None
    return str(row[0])


def test_cycle_persists_and_publishes(db: psycopg.Connection) -> None:
    channel_id = _channel(db)
    result = run_cycle(
        PgCycleStore(db),
        goal_ref="engagement_depth",
        targets=[
            CycleTarget(
                channel_id=channel_id,
                platform="instagram",
                content_format="feed_image",
                mode="auto",
            )
        ],
        model=ScriptedChatModel(messages=iter(_script())),
        research_trends=FakeResearchTrends(),
        read_stats=FakeReadStats(),
        render_media=FakeRenderMedia(),
        assess_quality=_passing,
    )
    assert result.status == "completed"
    assert len(result.prepared) == 1

    # 적재 검증: content_item.media_spec(jsonb) 왕복, media_asset.quality_status.
    (spec_row,) = db.execute("SELECT media_spec, hook_pattern FROM content_item").fetchall()
    assert spec_row[0] == _CARD_SPEC
    assert spec_row[1] == "curiosity"
    (q,) = db.execute("SELECT quality_status FROM media_asset").fetchall()
    assert q[0] == "passed"
    (pub,) = db.execute("SELECT status FROM publication").fetchall()
    assert pub[0] == "pending"

    # 무인 관통: 기존 발행 러너가 pending을 published로 종결.
    outcomes = run_pending_publications(db, FakePublish())
    assert [r.outcome for r in outcomes] == ["published"]
    (final,) = db.execute("SELECT status, external_post_id FROM publication").fetchall()
    assert final[0] == "published"
    assert final[1] is not None


def test_cycle_started_and_completed_events(db: psycopg.Connection) -> None:
    channel_id = _channel(db)
    result = run_cycle(
        PgCycleStore(db),
        goal_ref="reach",
        targets=[
            CycleTarget(
                channel_id=channel_id,
                platform="instagram",
                content_format="feed_image",
                mode="auto",
            )
        ],
        model=ScriptedChatModel(messages=iter(_script())),
        research_trends=FakeResearchTrends(),
        read_stats=FakeReadStats(),
        render_media=FakeRenderMedia(),
        assess_quality=_passing,
    )
    kinds = [
        r[0]
        for r in db.execute(
            "SELECT kind FROM run_event WHERE cycle_id = %s ORDER BY created_at", (result.cycle_id,)
        ).fetchall()
    ]
    assert kinds[0] == "cycle_started"
    assert kinds[-1] == "cycle_completed"
    (status,) = db.execute("SELECT status FROM cycle WHERE id = %s", (result.cycle_id,)).fetchone()
    assert status == "completed"
