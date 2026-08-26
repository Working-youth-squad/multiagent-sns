"""키워드 챗봇 대화 에이전트 (FR-W6 [신설]) — deepagents 기반, 툴 2개.

한 턴의 입력은 `(지금까지의 대화, 사용자 발화)`이고 출력은 `ChatTurn` 하나다. 저장은
호출자(웹 앱) 몫이다 — [sns.agents.content]·[sns.agents.topic]과 같은 규율.

**이 모듈에서 가장 중요한 결정: LLM에게 통계 숫자를 주지 않는다.**

`search_keywords` 툴은 `rank_keywords`를 부르고, 그 결과 **전량**을 코드가 포착해
`ChatTurn.rankings`로 돌려준다(화면·DB가 쓰는 정본). 그런데 LLM에게 돌려주는 문자열은
숫자가 빠진 요약이다 — 키워드 이름, 그 키워드를 아는 소스 수, 필터 상태 문장뿐이다.

이유는 관측된 실패 양식이다. `rank_std`를 문장에 넣게 두면 LLM은 `None`("소스 1곳만
알아서 불일치를 잴 수 없다")을 "0.00, 완전 일치"로 옮겨 적는다. 두 사실은 정반대다.
`filter_mode` 3값도 마찬가지로 "걸렀습니다" 한마디로 뭉개진다 — `passthrough`(데이터가
적어 필터가 열리지 않음)를 그렇게 말하면 **필터 없는 척**이 된다.

숫자를 아예 손에 쥐여주지 않으면 이 오류가 생길 수 없다. 표는 화면이 원본에서 직접
그리고([sns.web.chat.render]), LLM은 그 위에 해설만 붙인다.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import tool

from sns.agents.topic import TopicResult
from sns.chat.store import ChatMessage
from sns.research import KeywordRanking, rank_keywords, ranking_to_dict
from sns.runner.formats import FormatChoice, parse_format_choice
from sns.tools.contracts import VideoMethod

RankFn = Callable[..., KeywordRanking]

# LLM에게 요약을 돌려줄 때 나열하는 후보 상한. 전량을 문장으로 흘리면 모델이 그중
# 일부만 골라 "이게 전부"처럼 말하는 편이라, 잘렸다는 사실을 명시하고 자른다.
_SUMMARY_LIMIT = 10

# 대화 이력을 LLM에 실을 때의 상한(메시지 수). 폼 POST 방식이라 대화가 길어져도
# 서버가 매 턴 전량을 읽는데, 그걸 그대로 모델에 넣으면 토큰이 선형으로 는다.
HISTORY_LIMIT = 20

METHOD_LABELS: dict[VideoMethod, str] = {
    "template": "템플릿 — 스톡 사진과 타이포로 조립한다. 추가 비용 없음",
    "generated_scene": "장면 생성 — 컷마다 AI가 이미지를 만든다. **유료**",
    "generated_clip": "클립 생성 — 컷 배경을 AI가 영상으로 만든다. **유료(가장 비쌈)**",
    "hybrid": "혼합 — 컷마다 방식이 다르다",
}

FORMAT_LABELS: dict[FormatChoice, str] = {
    "card": "카드 — 피드에 올리는 정사각/세로 이미지 한 장",
    "video": "영상 — 세로 쇼츠·릴스(나레이션 포함)",
}

_PROMPT_HEAD = """당신은 SNS 성장 엔진의 키워드 상담 챗봇이다. 사용자가 막연한 관심사를 \
가져오면 대화로 좁혀서, 근거 있는 트렌드 키워드를 찾아주고, 사용자가 원하면 그 주제로 \
콘텐츠 초안 제작까지 연결한다.

도구:
- search_keywords(query): 네이버·구글·유튜브 자동완성 3소스에서 그 질의어의 연관 키워드를
  모아 등수 통계를 낸다. 질의어는 한 번에 하나이고, 짧은 명사구가 잘 나온다.
- confirm_topic(title, summary, category, content_format, method): 사용자가 "이걸로 만들어줘"
  라고 확정했을 때만 부른다. 부르는 순간 콘텐츠 초안 제작이 시작된다.

«CAPABILITIES»

규칙:
1. **숫자를 지어내지 않는다.** 등수·표준편차·점수 같은 수치는 화면이 도구 결과 원본에서
   직접 표로 그린다. 당신은 표를 다시 읽어주지 말고, 무엇을 골랐고 왜인지만 말한다.
2. **"걸렀습니다"라고 뭉개지 않는다.** 도구가 필터 상태를 문장으로 돌려준다. 필터가 열리지
   않았으면(데이터가 적어서) 그 사실을 그대로 전한다 — 거른 것처럼 말하면 거짓이 된다.
3. 소스가 실패했으면 숨기지 않고 "어느 소스가 빠진 결과"인지 말한다.
4. 사용자의 첫 발화가 이미 명확한 질의어면 되묻지 말고 바로 search_keywords를 부른다.
   막연하면("뭐 만들지") 한 번만 되묻고, 그 답으로 검색한다.
5. confirm_topic은 사용자가 명시적으로 고른 뒤에만 부른다. 먼저 제안하는 것은 좋지만
   동의 없이 확정하지 않는다. title은 콘텐츠 제목이 될 한 줄, summary는 무엇을 다룰지
   한두 문장이다.
6. **confirm_topic을 부른 뒤 콘텐츠를 직접 쓰지 않는다.** 초안·개요·목차·본문은 전부
   콘텐츠 제작 담당의 몫이다. 당신이 여기서 초안을 써 보이면 실제로 발행 승인 화면에
   올라갈 글과 달라서, 사용자는 어느 쪽이 나갈지 알 수 없게 된다. 확정했다는 사실과
   어디서 확인하면 되는지만 한두 줄로 알린다.
7. **위 목록에 없는 포맷·제작 방식은 제안하지 않는다.** 목록은 이 서버가 실제로 배선한
   것뿐이다 — 없는 것을 권하면 사용자는 도구가 거부할 선택을 고르게 된다.
8. **유료 표시가 붙은 방식은 그 사실을 먼저 말하고** 사용자가 그래도 좋다고 한 뒤에 쓴다.
9. 한국어로, 짧게 답한다."""


def _capabilities_block(formats: Sequence[FormatChoice], methods: Sequence[VideoMethod]) -> str:
    """이 서버가 **실제로 배선한** 선택지만 프롬프트에 적는다.

    Capability Gate([sns.render.video.router])를 대화까지 끌고 온 자리다. 라우터가
    거부할 방식을 LLM이 권하면 사용자는 막다른 길을 고르게 된다 — 애초에 안 보이는
    편이 낫다.
    """
    lines = ["이 서버가 만들 수 있는 것 (여기 없는 것은 고를 수 없다):", "", "content_format:"]
    lines += [f"- {f}: {FORMAT_LABELS[f]}" for f in formats]
    if "video" not in formats:
        lines.append("")
        lines.append(
            "영상은 이 서버에 배선돼 있지 않다(ffmpeg 또는 나레이션 자격증명 없음) — "
            "사용자가 영상을 원하면 만들 수 없다고 말한다."
        )
        return "\n".join(lines)
    lines += ["", "method (content_format=video일 때만 쓴다):"]
    lines += [f"- {m}: {METHOD_LABELS.get(m, m)}" for m in methods]
    return "\n".join(lines)


def _system_prompt(formats: Sequence[FormatChoice], methods: Sequence[VideoMethod]) -> str:
    return _PROMPT_HEAD.replace("«CAPABILITIES»", _capabilities_block(formats, methods))


@dataclass(frozen=True)
class SeedRequest:
    """사용자가 대화에서 확정한 것 전부 — 주제 + **무엇으로 만들지**.

    `TopicResult` 하나만 넘기던 자리를 대신한다. 포맷·제작 방식이 주제와 함께 대화에서
    정해지는데 시그니처가 주제만 나르면, 배선이 그 선택을 알 길이 없어 진입점이 포맷을
    하드코딩하게 된다 — 실제로 그랬다(`content_format="feed_image"` 고정).

    `method`가 `None`인 것은 "기본값"이 아니라 **카드에는 제작 방식이라는 축이 없다**는
    뜻이다. 영상일 때만 값이 있다.
    """

    topic: TopicResult
    content_format: FormatChoice = "card"
    method: VideoMethod | None = None


@dataclass
class ChatTurn:
    """한 턴의 산출 — 호출자가 이 내용을 대화에 append 한다."""

    reply: str
    """LLM의 마지막 발화. role='assistant'로 저장된다."""

    rankings: list[KeywordRanking] = field(default_factory=list)
    """이 턴에 수행된 랭킹 전량(툴 호출 순서). role='ranking'으로 원본 박제된다."""

    seed_request: SeedRequest | None = None
    """사용자가 확정한 주제·포맷·방식. 있으면 호출자가 콘텐츠 사이클을 띄운다(FR-W5)."""


def summarize_for_model(ranking: KeywordRanking) -> str:
    """LLM에게 돌려줄 요약 — **수치 없음**. 모듈 docstring이 그 이유다.

    소스 수(`present_count`)는 남긴다. "몇 곳이 아는 키워드인가"는 정수 사실이라
    오독될 여지가 없고, 이게 없으면 LLM이 후보들을 구별할 근거가 사라진다.
    """
    mode = {
        "active": "3소스 교차 분석으로 걸러낸 결과입니다.",
        "passthrough": "데이터가 적어 필터가 열리지 않았습니다 — 거르지 않고 전부입니다.",
        "off": "필터를 끈 전체 목록입니다.",
    }[ranking.filter_mode]

    lines = [f"질의어 '{ranking.query}' 결과.", f"필터 상태: {mode}"]
    if ranking.sources_failed:
        lines.append(
            f"실패한 소스: {', '.join(ranking.sources_failed)}"
            f" (성공: {', '.join(ranking.sources_ok) or '없음'})"
        )
    if not ranking.candidates:
        lines.append("후보 없음.")
        return "\n".join(lines)

    shown = ranking.candidates[:_SUMMARY_LIMIT]
    lines.append(
        f"후보 {len(ranking.candidates)}건"
        + (f" 중 {len(shown)}건:" if len(shown) < len(ranking.candidates) else ":")
    )
    for i, c in enumerate(shown, 1):
        lines.append(f"{i}. {c.text} (소스 {c.present_count}곳)")
    lines.append("표는 화면이 이미 원본으로 그렸다. 수치를 다시 말하지 말 것.")
    return "\n".join(lines)


def to_langchain_messages(history: Sequence[ChatMessage]) -> list[BaseMessage]:
    """저장된 대화 → LLM 이력.

    `ranking`·`system` 역할은 **싣지 않는다**. 랭킹 원본을 이력에 넣으면 규율 위반의
    재료(수치)를 모델 손에 되돌려주는 셈이고, 그 턴의 요약은 이미 툴 결과로 모델이
    본 내용이다. 대신 무엇을 검색했는지는 잃지 않게 한 줄로 남긴다.
    """
    out: list[BaseMessage] = []
    for m in history[-HISTORY_LIMIT:]:
        if m.role == "user":
            out.append(HumanMessage(m.body))
        elif m.role == "assistant":
            out.append(AIMessage(m.body))
        elif m.role == "ranking":
            query = "" if m.payload is None else str(m.payload.get("query", ""))
            out.append(AIMessage(f"(앞서 '{query}' 키워드 표를 사용자에게 보여줬다)"))
    return out


def run_chat_turn(
    model: BaseChatModel,
    *,
    history: Sequence[ChatMessage],
    user_text: str,
    rank_fn: RankFn = rank_keywords,
    exclude: Sequence[str] | None = None,
    formats: Sequence[FormatChoice] = ("card",),
    methods: Sequence[VideoMethod] = ("template",),
) -> ChatTurn:
    """한 턴 진행. 툴 호출 결과는 코드가 포착해 `ChatTurn`으로 돌려준다.

    `rank_fn`은 테스트가 네트워크 없이 도는 지점이다(`rank_keywords`와 같은 시그니처).
    `exclude`는 채널 프로필에서 온 제외어 — [sns.research.keywords]가 이 목록을 판정하지
    않고 호출자에게 맡긴 그 자리다.

    `formats`·`methods`는 **이 서버가 배선한 것**이다(Capability Gate를 대화까지). 기본값이
    카드·템플릿뿐인 것은 그 둘만이 추가 전제 없이 항상 도는 조합이기 때문이다 — 영상은
    ffmpeg과 나레이션 자격증명을, 생성 방식은 결제를 요구한다. 배선이 명시로 넓힌다.
    """
    if not user_text.strip():
        raise ValueError("사용자 발화가 비어 있다")
    if not formats:
        raise ValueError("formats가 비어 있다 — 만들 수 있는 것이 없으면 대화할 수 없다")
    if "video" in formats and not methods:
        raise ValueError("영상을 배선했는데 methods가 비어 있다 — 라우터가 아무것도 못 고른다")

    rankings: list[KeywordRanking] = []
    seed: dict[str, str] = {}
    choice: dict[str, object] = {}

    @tool
    def search_keywords(query: str, min_present: int = 1) -> str:
        """질의어의 연관 트렌드 키워드를 3소스에서 모아 등수 통계로 정렬한다.

        min_present=2면 2개 이상 소스가 아는 키워드만 남긴다(교차검증).
        """
        target = query.strip()
        if not target:
            return "오류: 질의어가 비어 있다."
        if min_present < 1:
            return f"오류: min_present는 1 이상이어야 한다 (받음: {min_present})"
        try:
            ranking = rank_fn(target, min_present=min_present, exclude=exclude)
        except Exception as exc:  # 소스 전멸·타임아웃 등 — 대화는 계속돼야 한다
            return f"오류: 키워드 조회 실패 — {exc}"
        rankings.append(ranking)
        return summarize_for_model(ranking)

    @tool
    def confirm_topic(
        title: str,
        summary: str,
        category: str = "tool",
        content_format: str = "card",
        method: str = "",
    ) -> str:
        """사용자가 확정한 주제로 콘텐츠 초안 제작을 시작한다. 동의 없이 부르지 않는다.

        content_format은 'card' 또는 'video'. method는 영상일 때의 제작 방식이다 —
        시스템 프롬프트의 목록에 있는 값만 받는다.
        """
        if not title.strip() or not summary.strip():
            return "오류: title과 summary가 모두 필요하다."

        fmt = parse_format_choice(content_format.strip().lower())
        if fmt is None or fmt not in formats:
            # 배선 밖의 값은 되돌려 보낸다 — 여기서 카드로 떨구면 사용자가 영상을
            # 골랐는데 이미지가 나오고, 왜 그런지는 아무 데도 안 남는다.
            return (
                f"오류: content_format {content_format!r}은 이 서버가 만들 수 없다. "
                f"고를 수 있는 것: {list(formats)}"
            )

        picked: VideoMethod | None = None
        if fmt == "video":
            name = method.strip() or methods[0]
            if name not in methods:
                return (
                    f"오류: method {method!r}은 이 서버가 배선하지 않았다. "
                    f"고를 수 있는 것: {list(methods)}"
                )
            picked = name  # type: ignore[assignment]

        seed.update(
            title=title.strip(), summary=summary.strip(), category=category.strip() or "tool"
        )
        choice.update(content_format=fmt, method=picked)
        # 카드에 method를 실어 보내는 것은 오류가 아니라 축 착각이다 — 거부하면 LLM이
        # 같은 호출을 반복한다. 무시했다는 사실만 분명히 밝힌다.
        ignored = (
            " (카드에는 제작 방식이라는 축이 없어 method는 무시했다)"
            if (fmt == "card" and method.strip())
            else ""
        )
        return (
            f"주제 확정 — {fmt}{'' if picked is None else f'/{picked}'}로 초안 제작이 "
            f"시작됐다.{ignored} **초안을 직접 쓰지 말 것** — 확정 사실과 승인 화면에서 "
            "확인하면 된다는 안내만 한두 줄로 하고 끝낸다."
        )

    agent = create_deep_agent(
        model=model,
        tools=[search_keywords, confirm_topic],
        system_prompt=_system_prompt(formats, methods),
    )
    messages: list[BaseMessage] = [*to_langchain_messages(history), HumanMessage(user_text)]
    state = agent.invoke({"messages": messages})  # type: ignore[call-overload]
    reply = _message_text(state["messages"][-1].content)

    seed_request = None
    if seed:
        topic = TopicResult(
            title=seed["title"],
            category=seed["category"],
            # 출처는 "챗봇 대화"다. 트렌드 소스 이름을 적으면 원장에서 자동 수집분과
            # 구별되지 않아 auto vs hybrid 비교(FR-E4)의 근거가 섞인다.
            source="chat_seed",
            summary=seed["summary"],
            reason="사용자가 대화에서 직접 확정",
        )
        seed_request = SeedRequest(
            topic=topic,
            content_format=choice["content_format"],  # type: ignore[arg-type]
            method=choice["method"],  # type: ignore[arg-type]
        )

    return ChatTurn(reply=reply, rankings=rankings, seed_request=seed_request)


def _message_text(content: object) -> str:
    """메시지 content → 텍스트 ([sns.agents.content._message_text]와 같은 사정).

    일부 모델은 문자열이 아니라 콘텐츠 블록 리스트를 반환한다.
    """
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


def ranking_payload(ranking: KeywordRanking) -> dict[str, object]:
    """DB에 박제할 모양 — `ranking_to_dict` 그대로. 반올림·요약하지 않는다."""
    return ranking_to_dict(ranking)
