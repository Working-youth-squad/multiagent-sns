"""분석글 착지 × PgMetricStore 관통 — 발행 원장에서 analysis_note까지 (FR-L4·L5).

순수 테스트(test_analysis_report)가 분기를 다 겨누므로 여기서는 **관통 두 줄**만 본다:
통과한 글이 테이블에 앉는가, 거부된 글이 테이블을 비워 두는가. 인메모리에서 통과한
계약이 실제 SQL(FK·CHECK·버전 채번)에서도 성립하는지가 이 파일의 존재 이유다.
"""

from typing import Any

import psycopg
from langchain_core.messages import AIMessage

from sns.learning.report import write_analysis_note
from sns.learning.schedule import REWARD_WINDOW_INDEX
from sns.learning.stores import PgMetricStore
from sns.tools.contracts import MetricValue
from tests.conftest import SeedFn
from tests.test_analyst_agent import ScriptedChatModel

_HONEST_BODY = (
    "평균 시청 시간은 42초였습니다. 기준선 표본이 부족해 판정 불가입니다. "
    "동일 품질의 콘텐츠도 조회수가 10배 차이 날 수 있습니다."
)
_DISHONEST_BODY = "공유율이 37.5%로 급등했습니다. 조회수는 10배 차이 날 수 있습니다."

_PLAYBOOK_CALL: list[dict[str, Any]] = [
    {
        "name": "write_playbook_tool",
        "args": {"scope": "platform", "guidance": "훅을 질문형으로", "scope_ref": "youtube"},
        "id": "call_1",
    }
]


def _model(body: str, *, tool_calls: list[dict[str, Any]] | None = None) -> ScriptedChatModel:
    messages = [AIMessage(content="", tool_calls=tool_calls)] if tool_calls else []
    messages.append(AIMessage(content=body))
    return ScriptedChatModel(messages=iter(messages))


def _published_with_metrics(db: psycopg.Connection, seed: SeedFn, *, post_id: str) -> str:
    """발행 완료 1건 + 그 창의 관측 1건. 분석 표본이 되는 최소 상태."""
    pub_id = seed(platform="youtube", fmt="shorts")
    db.execute(
        """
        UPDATE publication
           SET status = 'published', external_post_id = %s, published_at = now()
         WHERE id = %s
        """,
        (post_id, pub_id),
    )
    PgMetricStore(db).save_observation(
        publication_id=pub_id,
        window_index=REWARD_WINDOW_INDEX,
        values=[
            MetricValue("avg_view_duration_s", 42.0, False),
            MetricValue("views", None, True),
        ],
    )
    return pub_id


def test_validated_note_lands_in_the_ledger(db: psycopg.Connection, seed: SeedFn) -> None:
    _published_with_metrics(db, seed, post_id="vid-1")
    report = write_analysis_note(
        _model(_HONEST_BODY, tool_calls=_PLAYBOOK_CALL), PgMetricStore(db), platform="youtube"
    )

    assert report.note_id is not None
    assert report.insufficient_evidence  # 기준선 0건 — 정직 결측 경로
    rows = db.execute("SELECT body, insufficient_evidence FROM analysis_note").fetchall()
    assert rows == [(_HONEST_BODY, True)]
    # 지침은 검증 통과 후에 흘러갔고, 버전은 저장소가 매긴다(임시 순번이 아니라).
    assert db.execute("SELECT scope, scope_ref, version, guidance FROM playbook").fetchall() == [
        ("platform", "youtube", 1, "훅을 질문형으로")
    ]


def test_rejected_note_leaves_both_tables_empty(db: psycopg.Connection, seed: SeedFn) -> None:
    """DoD — analysis_note 0건 + run_event 1건. 지침도 함께 버려진다."""
    _published_with_metrics(db, seed, post_id="vid-1")
    report = write_analysis_note(
        _model(_DISHONEST_BODY, tool_calls=_PLAYBOOK_CALL), PgMetricStore(db), platform="youtube"
    )

    assert report.note_id is None and report.rejected_reasons
    assert db.execute("SELECT count(*) FROM analysis_note").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM playbook").fetchone() == (0,)
    events = db.execute("SELECT kind, payload FROM run_event").fetchall()
    assert len(events) == 1
    kind, payload = events[0]
    assert kind == "error"
    assert payload["reason"] == "analysis_rejected"
    assert payload["dropped_playbook_entries"] == 1
