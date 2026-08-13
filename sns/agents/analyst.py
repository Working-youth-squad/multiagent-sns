"""Analyst 에이전트 (FR-A1·L5, 03 §2) — 신호 해석·분석글, deepagents 기반.

역할 분담: 수치는 전부 도구 내부 코드가 계산하고(스코어보드·드릴다운), LLM은
도구를 골라 쓰며 해석·서술만 한다. 조사 순서·플레이북 갱신 여부는 에이전트의
재량 — 코드에 순서를 박지 않는다. 산출물(body)은 검증기(FR-L5)를 통과해야만
반환되고, `insufficient_evidence`는 LLM이 아니라 코드가 결정한다.

착지점: body → analysis_note.body(저장은 러너 몫), write_playbook 툴 → playbook.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from sns.learning.validator import validate_analysis
from sns.signals.scoreboard import (
    PostSignals,
    compute_scoreboard,
    scoreboard_json,
    signal_values,
)
from sns.tools.contracts import (
    Platform,
    PlaybookScope,
    PollMetrics,
    ReadStats,
    WritePlaybook,
)

_SYSTEM_PROMPT = """당신은 SNS 성장 엔진의 Analyst 에이전트다. \
게시물 성과를 해석해 짧은 분석글을 쓴다.

규칙(위반 시 분석글이 기계 검증기에 거부된다):
1. 수치는 도구가 반환한 JSON에 있는 값만 인용한다. 새 숫자를 만들지 않는다.
2. verdict_available이 false면 "판정 불가"를 명시하고 결론을 내리지 않는다.
3. 어떤 경우에도 분산 경고를 포함한다: "동일 품질의 콘텐츠도 조회수가 10배 차이 날 수 있습니다".
4. 근거 없는 인과 단정 금지 — 가설은 "~일 수 있으나 단정할 수 없다"로 쓴다.
5. 충분한 근거(기준선 대비 뚜렷한 상·하위 패턴)가 있을 때만 write_playbook_tool로 지침을 남긴다.

도구: read_scoreboard(기준 게시물 스코어보드), poll_window(다른 시간 창 드릴다운),
read_topic_stats(주제별 누적 통계), write_playbook_tool(제작 지침 기록).
조사 순서는 스스로 정하되, 마지막 메시지가 곧 분석글 본문이다. 한국어로 쓴다."""


@dataclass(frozen=True)
class AnalysisResult:
    body: str  # analysis_note.body 착지 예정 (FR-C4)
    insufficient_evidence: bool  # 코드 결정 — LLM 아님
    playbook_written: bool


class AnalysisRejected(RuntimeError):
    """검증기(FR-L5) 거부 — 재시도 없이 즉시 실패.

    # ponytail: 재시도 루프는 실거부율 확인 후 추가.
    """

    def __init__(self, reasons: tuple[str, ...], body: str) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons
        self.body = body  # 거부된 본문 — 디버깅·거부율 분석용


def _message_text(content: object) -> str:
    """메시지 content → 텍스트. 일부 모델(Gemini 3.x)은 콘텐츠 블록 리스트를 반환 —
    str() 변환 시 서명 토큰 등 메타데이터가 본문에 섞여 검증기가 오염되므로
    text 블록만 추출한다."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


def run_analysis(
    model: BaseChatModel,
    *,
    platform: Platform,
    post_ids: Sequence[str],
    window_index: int,
    poll_metrics: PollMetrics,
    read_stats: ReadStats,
    write_playbook: WritePlaybook,
) -> AnalysisResult:
    """post_ids 마지막 게시물을 나머지(기준선) 대비 분석. 검증 통과한 글만 반환."""
    if not post_ids:
        raise ValueError("분석할 post_id가 없음")

    # 수치는 LLM 개입 전에 코드가 계산 (FR-L5).
    def signals_of(post_id: str, window: int) -> PostSignals:
        metrics = {v.metric_key: v.value for v in poll_metrics(platform, post_id, window)}
        return PostSignals(post_id=post_id, values=signal_values(platform, metrics))

    *baseline_ids, target_id = post_ids
    target = signals_of(target_id, window_index)
    others = [signals_of(p, window_index) for p in baseline_ids]
    sb = compute_scoreboard(platform, target, others, window_index=window_index)
    sb_json = scoreboard_json(sb)
    playbook_written = False

    @tool
    def read_scoreboard() -> str:
        """기준 게시물의 신호 스코어보드(JSON). 인용 가능한 수치의 유일한 출처."""
        return sb_json

    @tool
    def poll_window(post_id: str, window: int) -> str:
        """다른 게시물/시간 창(0=6h,1=24h,2=72h)의 신호값 드릴다운(JSON, 코드 계산)."""
        return json.dumps(
            {
                "post_id": post_id,
                "window_index": window,
                "signals": signals_of(post_id, window).values,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @tool
    def read_topic_stats() -> str:
        """주제×포맷별 누적 통계(JSON)."""
        rows = read_stats(platform)
        return json.dumps(
            [
                {
                    "topic_id": r.topic_id,
                    "format": r.format,
                    "trials": r.trials,
                    "reward_sum": r.reward_sum,
                }
                for r in rows
            ],
            ensure_ascii=False,
            sort_keys=True,
        )

    @tool
    def write_playbook_tool(scope: str, guidance: str, scope_ref: str | None = None) -> str:
        """제작 지침을 플레이북에 기록. 충분한 근거가 있을 때만 호출."""
        nonlocal playbook_written
        if scope not in ("global", "platform", "format", "topic"):
            return f"오류: 잘못된 scope {scope}"
        version = write_playbook(_as_scope(scope), guidance, scope_ref)
        playbook_written = True
        return f"기록됨 (version {version.version})"

    agent = create_deep_agent(
        model=model,
        tools=[read_scoreboard, poll_window, read_topic_stats, write_playbook_tool],
        system_prompt=_SYSTEM_PROMPT,
    )
    state = agent.invoke(
        {
            "messages": [
                HumanMessage(
                    f"플랫폼 {platform}, 게시물 {target_id}"
                    f"(관측 창 {window_index})의 성과를 분석하라."
                )
            ]
        }
    )
    body = _message_text(state["messages"][-1].content)

    result = validate_analysis(
        body,
        scoreboard_json=sb_json,
        post_ids=set(post_ids),
        verdict_available=sb.verdict_available,
    )
    if not result.ok:
        raise AnalysisRejected(result.reasons, body)
    return AnalysisResult(
        body=body,
        insufficient_evidence=not sb.verdict_available,
        playbook_written=playbook_written,
    )


def _as_scope(scope: str) -> PlaybookScope:
    assert scope in ("global", "platform", "format", "topic")
    return scope  # type: ignore[return-value]
