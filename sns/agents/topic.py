"""Topic 에이전트 (FR-G1·G4, 04 §4) — 개발자 주제 발굴, deepagents 기반.

역할: `research_trends`(외부 트렌드 = 근거)와 `read_stats`(과거 성과)를 도구로 읽고,
카테고리 5종 중 하나로 주제 **1건을 선택**한다. 선택 원칙(설명 난이도 하한·호기심
후크, 04 §4)은 프롬프트로 위임하되, **제목은 트렌드 다이제스트에서 온 근거 있는
후보 중에서만** 고르게 한다(할루시네이션 방지, FR-G4). LLM이 수치·후보를 지어내지
않도록 선택은 `choose_topic` 툴로 코드가 포착하고, 후보 인덱스·카테고리 유효성은
코드가 검증한다 — 여기서 "에이전트"는 자율 재량이 아니라 근거 위의 *선택자*다.

착지점: 없음. topic은 LLM 착지점 3곳(FR-C4)에 속하지 않는 메타데이터이며, 선택
결과(TopicResult)만 코드가 조립한다. DB 저장은 러너 몫(analyst.py와 동일 규율).
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from sns.tools.contracts import Platform, ReadStats, ResearchTrends, SourceResult

# 주제 카테고리 5종 (04 §4 · 11-데이터모델 content_item.topic_category).
TopicCategory = Literal["신기술", "기초지식", "꿀팁", "현직자일상", "개발자유머"]
TOPIC_CATEGORIES: tuple[str, ...] = get_args(TopicCategory)

_SYSTEM_PROMPT = """당신은 SNS 성장 엔진의 Topic 에이전트다. \
개발자 대상 콘텐츠의 주제를 딱 하나 고른다.

카테고리(반드시 이 중 하나): 신기술 · 기초지식 · 꿀팁 · 현직자일상 · 개발자유머.

선정 원칙:
1. 설명 난이도 하한 — 한 컷/한 카드로 이해되는 주제만. 배경지식이 3개 이상 필요하면 고르지 않는다.
2. 호기심 후크 — 첫 화면에서 "왜?·헐·나도"를 유발할 수 있는 주제를 우선한다.
3. 근거 — read_trends가 준 후보 index 중에서만 고른다. 목록에 없는 주제는 지어내지 않는다.
4. 과거 성과(read_topic_stats)를 참고하되, 표본이 없으면 다양성을 위해 새 카테고리를 시도해도 된다.

도구: read_trends(근거 있는 후보 목록), read_topic_stats(주제별 누적 성과),
choose_topic(고른 후보의 index·카테고리·한 줄 요약). 반드시 choose_topic을 호출해 확정한다.
조사 순서는 스스로 정한다. 한국어로 판단한다."""


@dataclass(frozen=True)
class TopicCandidate:
    index: int
    text: str
    source: str


@dataclass(frozen=True)
class TopicResult:
    title: str  # 후보 다이제스트에서 온 근거 있는 제목
    category: TopicCategory
    source: str  # 후보의 출처 트렌드 소스
    summary: str  # 에이전트가 붙인 한 줄 요약
    reason: str  # 선택 근거(관측·디버깅용)


class TopicSelectionError(RuntimeError):
    """에이전트가 유효한 주제를 확정하지 못함 — 재시도 없이 즉시 실패."""


def _candidates(digest_sources: Sequence[SourceResult]) -> tuple[TopicCandidate, ...]:
    """트렌드 다이제스트의 성공 소스 아이템을 중복 제거해 인덱스 부여."""
    seen: set[str] = set()
    out: list[TopicCandidate] = []
    for sr in digest_sources:
        if not sr.ok:
            continue
        for item in sr.items:
            if item in seen:
                continue
            seen.add(item)
            out.append(TopicCandidate(index=len(out), text=item, source=sr.source))
    return tuple(out)


def run_topic(
    model: BaseChatModel,
    *,
    platform: Platform,
    research_trends: ResearchTrends,
    read_stats: ReadStats,
    limit: int = 10,
) -> TopicResult:
    """트렌드 근거 + 과거 성과로 주제 1건을 선택. 유효 선택만 반환."""
    digest = research_trends(limit=limit)
    candidates = _candidates(digest.source_results)
    if not candidates:
        # 모든 소스 실패 = 콜드 스타트 주제 경로 없음. 조용히 지어내지 않고 실패.
        raise TopicSelectionError("트렌드 후보가 0건 — 모든 소스 실패로 주제 발굴 불가")

    chosen: dict[str, object] = {}

    @tool
    def read_trends() -> str:
        """근거 있는 주제 후보 목록(JSON). 여기 index 중에서만 골라야 한다."""
        return json.dumps(
            [{"index": c.index, "text": c.text, "source": c.source} for c in candidates],
            ensure_ascii=False,
            sort_keys=True,
        )

    @tool
    def read_topic_stats() -> str:
        """주제×포맷별 누적 성과(JSON). 표본이 없으면 빈 배열."""
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
    def choose_topic(index: int, category: str, summary: str) -> str:
        """고른 후보 index·카테고리·한 줄 요약으로 주제를 확정한다."""
        if not isinstance(index, int) or not 0 <= index < len(candidates):
            return f"오류: index는 0..{len(candidates) - 1} 범위여야 함 (받음: {index!r})"
        if category not in TOPIC_CATEGORIES:
            return f"오류: category는 {list(TOPIC_CATEGORIES)} 중 하나여야 함 (받음: {category!r})"
        chosen["index"] = index
        chosen["category"] = category
        chosen["summary"] = summary
        return f"확정: {candidates[index].text} [{category}]"

    agent = create_deep_agent(
        model=model,
        tools=[read_trends, read_topic_stats, choose_topic],
        system_prompt=_SYSTEM_PROMPT,
    )
    state = agent.invoke(
        {
            "messages": [
                HumanMessage(
                    f"플랫폼 {platform}. read_trends 후보 중에서 개발자 주제 1건을 골라 "
                    "choose_topic으로 확정하라."
                )
            ]
        }
    )
    if "index" not in chosen:
        # 마지막 메시지를 근거로 남겨 거부율/원인 분석 (analyst.AnalysisRejected와 같은 규율).
        last = state["messages"][-1].content
        raise TopicSelectionError(f"choose_topic 미호출 — 주제 미확정. 마지막 메시지: {last!r}")

    cand = candidates[int(chosen["index"])]  # type: ignore[call-overload]
    return TopicResult(
        title=cand.text,
        category=_as_category(str(chosen["category"])),
        source=cand.source,
        summary=str(chosen["summary"]),
        reason=f"trend={cand.source}",
    )


def _as_category(value: str) -> TopicCategory:
    assert value in TOPIC_CATEGORIES
    return value  # type: ignore[return-value]
