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
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from sns.tools.contracts import Platform, ReadStats, ResearchTrends, SourceResult

# 주제 카테고리 5종 (04 §4 · 11-데이터모델 content_item.topic_category).
# 기본값 = 개발 채널. 온보딩된 채널은 run_topic(categories=...)로 자기 5종을 넘긴다.
TopicCategory = Literal["신기술", "기초지식", "꿀팁", "현직자일상", "개발자유머"]
TOPIC_CATEGORIES: tuple[str, ...] = get_args(TopicCategory)


def _system_prompt(categories: tuple[str, ...]) -> str:
    audience = "개발자 대상" if categories == TOPIC_CATEGORIES else "이 채널의"
    return f"""당신은 SNS 성장 엔진의 Topic 에이전트다. \
{audience} 콘텐츠의 주제를 딱 하나 고른다.

카테고리(반드시 이 중 하나): {" · ".join(categories)}.

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
    # 기본 5종 밖 카테고리(온보딩 채널)도 담아야 해서 str — 유효성은 run_topic이 검증.
    category: str
    source: str  # 후보의 출처 트렌드 소스
    summary: str  # 에이전트가 붙인 한 줄 요약
    reason: str  # 선택 근거(관측·디버깅용)


# 포함 관계로 같은 주제를 판정할 때 요구하는 최소 토큰 수. 한 단어짜리("python")가
# 다른 제목을 통째로 삼키는 걸 막는다.
MIN_SHARED_TOKENS = 2


class TopicSelectionError(RuntimeError):
    """에이전트가 유효한 주제를 확정하지 못함 — 재시도 없이 즉시 실패."""


def _tokens(text: str) -> frozenset[str]:
    """비교용 토큰 — 소문자, 구분자 제거. 'cursor/plugins'와 'Cursor plugins'가 같아진다."""
    return frozenset(t for t in re.split(r"[^0-9a-z가-힣]+", text.lower()) if t)


def _match_recent(candidate: str, recent: Sequence[str]) -> bool:
    """최근 발행 주제와 사실상 같은가 — **같은 말이거나 한쪽이 다른 쪽을 통째로 포함**.

    처음엔 자카드 유사도 임계값(0.6)을 썼는데 오히려 멀쩡한 후보를 죽였다. 짧고 정형화된
    제목은 토큰을 대부분 공유하기 때문이다 — "python 3.13 릴리스"와 "python 3.14 릴리스"가
    0.75로 같은 주제 취급됐다. 후보가 조용히 말라붙는 건 중복 발행보다 나쁘다(안 보인다).

    포함 관계는 규칙이 분명하다: 'cursor/plugins' ⊆ 'Cursor plugins 모음'은 같은 주제고,
    'topic-1'과 'topic-2'는 서로를 포함하지 않으니 다른 주제다.
    """
    tokens = _tokens(candidate)
    if len(tokens) < MIN_SHARED_TOKENS:
        return False
    for title in recent:
        other = _tokens(title)
        if len(other) < MIN_SHARED_TOKENS:
            continue
        if tokens <= other or other <= tokens:
            return True
    return False


def _candidates(
    digest_sources: Sequence[SourceResult], exclude_titles: Sequence[str] = ()
) -> tuple[TopicCandidate, ...]:
    """트렌드 다이제스트의 성공 소스 아이템을 중복 제거해 인덱스 부여.

    최근 발행한 주제는 **에이전트에게 보여주기 전에** 뺀다. 프롬프트로 "겹치지 마라"
    부탁하는 방식이 아니다 — 목록에 있으면 언젠가 고른다(통제=코드, 판단=LLM).
    """
    seen: set[str] = set()
    out: list[TopicCandidate] = []
    for sr in digest_sources:
        if not sr.ok:
            continue
        for item in sr.items:
            if item in seen or _match_recent(item, exclude_titles):
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
    exclude_titles: Sequence[str] = (),
    categories: Sequence[str] | None = None,
    guidance: str | None = None,
) -> TopicResult:
    """트렌드 근거 + 과거 성과로 주제 1건을 선택. 유효 선택만 반환.

    `exclude_titles`는 최근 발행한 주제다. 트렌드 소스는 같은 항목을 며칠씩 노출하므로
    이걸 안 빼면 어제와 같은 영상이 나간다(실제로 그랬다).

    `categories`·`guidance`는 온보딩된 채널의 프로필 주입점(기본 None = 기존 동작
    무변경): categories는 카테고리 5종 교체, guidance는 채널 주제 범위·컨셉 지침
    ([sns.onboarding.profile.build_channel_brief])이다.
    """
    active_categories = TOPIC_CATEGORIES if categories is None else tuple(categories)
    digest = research_trends(limit=limit)
    candidates = _candidates(digest.source_results, exclude_titles)
    if not candidates:
        # 모든 소스 실패 = 콜드 스타트 주제 경로 없음. 조용히 지어내지 않고 실패.
        if _candidates(digest.source_results):
            raise TopicSelectionError(
                "남은 후보 0건 — 트렌드 후보가 전부 최근 발행 주제와 겹친다. "
                "같은 주제를 또 내보내느니 이 사이클을 실패시킨다."
            )
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
        if category not in active_categories:
            return f"오류: category는 {list(active_categories)} 중 하나여야 함 (받음: {category!r})"
        chosen["index"] = index
        chosen["category"] = category
        chosen["summary"] = summary
        return f"확정: {candidates[index].text} [{category}]"

    agent = create_deep_agent(
        model=model,
        tools=[read_trends, read_topic_stats, choose_topic],
        system_prompt=_system_prompt(active_categories),
    )
    subject = "개발자 주제" if categories is None else "이 채널에 맞는 주제"
    request = (
        f"플랫폼 {platform}. read_trends 후보 중에서 {subject} 1건을 골라 "
        "choose_topic으로 확정하라."
    )
    if guidance:
        request = f"{guidance}\n\n{request}"
    state = agent.invoke({"messages": [HumanMessage(request)]})
    if "index" not in chosen:
        # 마지막 메시지를 근거로 남겨 거부율/원인 분석 (analyst.AnalysisRejected와 같은 규율).
        last = state["messages"][-1].content
        raise TopicSelectionError(f"choose_topic 미호출 — 주제 미확정. 마지막 메시지: {last!r}")

    cand = candidates[int(chosen["index"])]  # type: ignore[call-overload]
    category = str(chosen["category"])
    assert category in active_categories  # choose_topic이 이미 검증
    return TopicResult(
        title=cand.text,
        category=category,
        source=cand.source,
        summary=str(chosen["summary"]),
        reason=f"trend={cand.source}",
    )
