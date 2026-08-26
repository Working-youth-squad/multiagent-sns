"""Content 에이전트 (FR-G2·G3·G5, 05) — 포맷별 대본/문안 생성, deepagents 기반.

선택된 주제(TopicResult)를 받아 포맷별 산출을 만든다:
- 피드(feed_image) → 카드 `media_spec`(hook·title·body·footer)
- 릴스/쇼츠(reels·shorts) → 영상 `media_spec`(slides: 장면·자막·나레이션)

착지점(FR-C4): `body`(마지막 메시지) → content_item.body. 그 외 구조 산출은
LLM이 자유 JSON으로 흘리지 않고 **툴로 코드가 포착·검증**한다:
- `set_hook(pattern)` → hook_pattern 5종 검증(FR-G5, 훅 분리 생성).
- `set_media_spec(spec_json)` → 포맷에 맞는 기존 파서(parse_card_spec/parse_video_spec)로
  검증 — 렌더 진입 전 방어선을 생성 시점에 앞당겨, 잘못된 spec은 즉시 재시도시킨다.

저장은 러너 몫(analyst.py·topic.py와 동일 규율).
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from sns.agents.topic import TopicResult
from sns.render.card.spec import CardSpecError, parse_card_spec
from sns.render.video.spec import VideoSpecError, parse_video_spec
from sns.tools.contracts import ContentFormat

# 훅 패턴 5종 (FR-G5 · content_item.hook_pattern CHECK).
HookPattern = Literal["bold_claim", "curiosity", "story", "shock", "question"]
HOOK_PATTERNS: tuple[str, ...] = get_args(HookPattern)

# 채널마다 독자가 다르다(개발·요리·음악…) — {audience} 자리만 갈리고 규칙은 같다.
DEFAULT_AUDIENCE = "개발자"

_SYSTEM_PROMPT = """당신은 SNS 성장 엔진의 Content 에이전트다. \
주어진 {audience} 주제로 포맷에 맞는 콘텐츠를 만든다.

규칙:
1. 첫 화면(훅)과 본문을 분리한다. 훅은 스크롤을 멈출 이유를 첫 프레임/첫 카드에 담는다.
2. 훅 패턴을 하나 정해 set_hook으로 기록한다:
   bold_claim(단언)·curiosity(호기심)·story(이야기)·shock(충격)·question(질문).
3. media_spec을 set_media_spec에 JSON으로 넘긴다. 틀리면 오류가 돌아오니 고쳐 다시 호출한다.
   - 피드(feed_image): {"hook","title","body":[단락...],"footer"}
     **글자수 상한(한글 기준, 넘으면 카드가 깨져 거부된다)**:
     hook 32자 · title 24자 · body 단락당 60자 · body 최대 3단락 · footer 24자.
     이모지는 폰트에 없어 네모로 나오니 쓰지 않는다.
   - 릴스/쇼츠(reels/shorts):
     {"topic","slides":[{"subtitle","narration","code","lang","focus_lines"}...]}
     화면은 3단이다 — 위=주제·부제, 가운데=정사각 코드, 아래=자막(=나레이션).
     * topic: 영상 내내 화면 위에 고정되는 주제 한 줄. **한글 22자 이내.**
       시청자가 중간부터 봐도 무슨 영상인지 알게 하는 앵커다.
     * **슬라이드 1장 = 화면 1장.** 슬라이드마다 부제·코드·자막이 동시에 바뀐다.
     * subtitle: 이 컷이 무엇을 다루는지 알약에 들어갈 짧은 말. **한글 20자 이내.**
       topic을 되풀이하지 말고 흐름을 진행시켜라("왜 느린가" → "해법" → "결과").
     * narration: TTS 발화이자 하단 자막. **한글 31자 이내 한 문장.** 길면 그 화면이
       4초 넘게 멈춰 있어 거부된다. 슬라이드 수는 넉넉하니(최대 60장), 길게 설명할
       내용은 짧은 슬라이드 여러 장으로 나눠라(정보량을 줄이지 말 것).
     * code(선택): 가운데 정사각에 문법 강조되어 그려질 코드. **최대 18줄**,
       한 줄은 짧게(48자 이내 권장 — 길면 글자가 작아져 안 읽힌다). 비우면 배경만 나온다.
       나레이션이 코드를 가리키는 컷에는 넣어라 — 말과 화면이 같은 것을 가리켜야 한다.
     * lang(선택): pygments 렉서 이름("python","javascript","sql"…). 비우면 추측한다.
     * concept(선택): 코드가 없는 컷에 **우리가 그리는 그림**. 종류 6개뿐이고
       컷 성격에 맞는 걸 고른다. 다른 kind나 없는 필드를 쓰면 거부된다.
       **코드가 없는 주제(도구 소개·트렌드·커리어)라면 정사각을 비우지 말고 여기서 골라라.**
       같은 kind를 연속 컷에 쓰되 active만 옮기면 화면이 진행되는 것처럼 보인다.
       - 충격적인 수치·키워드 한 방:
         {"kind":"emphasis","tag":"최악의 경우","headline":"100억","sub":"십만 건 × 십만 건 비교"}
         headline은 한글 8자 이내. 숫자 하나면 가장 세다.
       - 느린 방법 vs 빠른 방법 도해(왜 빨라지는지·왜 나은지 보여주는 컷):
         {"kind":"compare","before_label":"list","before_note":"6번 비교",
          "after_label":"set","after_note":"1번 비교","footer":"O(n) -> O(1)"}
         비개발 주제도 같은 모양이다:
         {"kind":"compare","before_label":"실온 버터","before_note":"30분 대기",
          "after_label":"전자레인지","after_note":"10초 해동","footer":"질감은 실온이 승"}
         label은 짧은 이름(한글 8자 이내), footer는 전후 변화 한 줄.
       - 마무리 "기억하세요" 한 줄:
         {"kind":"remember","line":"반복문 안에서 in을 쓴다면","code":"set(...)"}
         line은 한글 17자 이내, code는 기억할 짧은 코드(선택).
       - 동작 원리·파이프라인(무엇이 어떤 순서로 일어나는지):
         {"kind":"flow","steps":["주제 한 줄 입력","AI가 대본 작성","영상·음성 합성"],"active":1}
         steps는 **최대 3개**, 각 한글 10자 이내. active(0-기반)는 지금 말하는 단계.
       - 기능·항목 나열:
         {"kind":"steps","items":["대본 자동 생성","영상 자동 합성","자막·TTS 자동"],"active":2}
         items는 **최대 4개**, 각 한글 12자 이내. active는 지금 말하는 항목.
       - 설치·시작 명령(도구 소개의 마무리):
         {"kind":"terminal","commands":["pip install foo"],"note":"깃허브에서 무료"}
         commands는 **최대 2줄**, 각 34자 이내. note는 한 줄 설명(선택).
     * image_query(선택): **물리적 대상**을 말하는 컷에만 쓰는 실사 스톡 검색어
       (영어 2~4단어). 데이터센터·모니터·서버처럼 실제로 사진이 존재하는 대상일 때만
       의미가 있다. **추상 개념에는 절대 쓰지 마라** — 검색어의 단어에만 반응해
       엉뚱한 사진이 온다(전에 "list vs set" 컷에 전선 사진이 붙었다). 개념은 concept이
       맡는다. 사람·얼굴·로고·정치·무기가 들어간 검색어는 게이트에 막힌다.
     * image_prompt(선택): **코드가 한 컷도 없는 영상에서만** 쓸 수 있는 생성 이미지
       구도 설명(영어 한 문장). 커리어·트렌드·도구 소개처럼 보여줄 코드가 없는 주제가
       그 자리다. 화풍(어두운 배경·플랫 벡터·글자 없음)은 코드가 붙이니 **무엇이 어떻게
       놓여 있는지만** 써라: "a lone figure walking toward a bright doorway".
       코드를 쓰는 영상이면 한 컷이라도 image_prompt를 넣는 순간 전체가 거부된다 —
       코드 영상의 핵심 컷은 숫자와 비교라 concept이 낫기 때문이다.
     * 한 컷에는 code·concept·image_query·image_prompt 중 **하나만** 쓴다(정사각은
       하나다). 코드가 1순위, 추상 개념이면 concept, 실제 사물이면 image_query다.
       마땅치 않으면 비워라 — 억지로 붙인 그림보다 빈 배경이 낫다.
     * focus_lines(선택): 지금 말하고 있는 코드 줄 번호 목록(1-기반). 나머지 줄은
       어둡게 눌린다. code 없이 쓰면 거부된다. 같은 코드를 초점만 바꿔 연속 컷으로
       보여주면 "지금 이 줄"이 전달된다.
4. **구체성은 근거에서 온다.** '근거 자료' 블록이 주어지면 거기 있는 사실·수치·순서를
   대본의 중심에 그대로 인용하라 — 그게 이 영상의 알맹이다. 근거 자료 밖의 측정하지 않은
   숫자("생산성 300% 향상"·"3배 빨라짐")는 {audience} 독자에게 신뢰를 잃으니 쓰지 않는다.
   근거가 하나도 없으면 숫자를 빼고 말로 쓴다.
5. 마지막 메시지는 발행 캡션/본문(body)이다 — 해시태그 포함 가능, 한국어로 쓴다.
   캡션은 마크다운을 렌더하지 않는 플랫폼에 올라가니 **, ##, --- 같은 기호를 쓰지 않는다.
6. 정치·특정인 비방·음란·불법 복제 소재는 쓰지 않는다 — 게이트에 걸려 발행이 막힌다.

set_hook과 set_media_spec을 모두 호출한 뒤 마지막 메시지로 본문을 마무리한다."""


@dataclass(frozen=True)
class ContentResult:
    body: str  # content_item.body 착지 예정 (FR-C4)
    hook_pattern: HookPattern
    media_spec: dict[str, object]  # 검증 통과한 결정론 렌더 스펙 (FR-G3)


class ContentRejected(RuntimeError):
    """훅/미디어 스펙 미확정 — 재시도 없이 즉시 실패(analyst.AnalysisRejected 규율)."""

    def __init__(self, reason: str, body: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.body = body


def _message_text(content: object) -> str:
    """메시지 content → 텍스트. 일부 모델은 콘텐츠 블록 리스트를 반환하므로 text만 추출."""
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


def _validate_spec(spec: object, fmt: ContentFormat) -> dict[str, object]:
    """포맷별 기존 파서로 media_spec 검증. 통과한 dict를 그대로 반환(jsonb 저장용)."""
    if not isinstance(spec, dict):
        raise ValueError("media_spec은 객체여야 함")
    if fmt == "feed_image":
        parse_card_spec(spec)  # 던지면 CardSpecError
    elif fmt in ("reels", "shorts"):
        parse_video_spec(spec)  # 던지면 VideoSpecError
    else:
        raise ValueError(f"미지원 포맷: {fmt}")
    return spec


def run_content(
    model: BaseChatModel,
    *,
    topic: TopicResult,
    content_format: ContentFormat,
    playbook_guidance: str | None = None,
    audience: str | None = None,
    research: str | None = None,
    avoid_titles: Sequence[str] = (),
) -> ContentResult:
    """주제 → 포맷별 콘텐츠. 훅·검증된 media_spec·본문 모두 확보돼야 반환.

    `research`는 [sns.agents.research]의 근거 노트 — 있으면 구체성의 원천이 된다.
    `avoid_titles`는 최근 다룬 주제 — 같은 각도의 재탕을 입력 단계에서 막는다
    (temperature=0 결정론은 유지하고, 다양성은 입력으로 만든다).
    """
    captured: dict[str, object] = {}

    @tool
    def set_hook(pattern: str) -> str:
        """훅 패턴을 기록한다(bold_claim/curiosity/story/shock/question 중 하나)."""
        if pattern not in HOOK_PATTERNS:
            return f"오류: pattern은 {list(HOOK_PATTERNS)} 중 하나여야 함 (받음: {pattern!r})"
        captured["hook"] = pattern
        return f"훅 기록: {pattern}"

    @tool
    def set_media_spec(spec_json: str) -> str:
        """포맷에 맞는 media_spec(JSON 문자열)을 검증·기록한다. 틀리면 오류 사유를 돌려준다."""
        try:
            spec = json.loads(spec_json)
        except (TypeError, ValueError) as exc:
            return f"오류: JSON 파싱 실패 — {exc}"
        try:
            captured["spec"] = _validate_spec(spec, content_format)
        except (CardSpecError, VideoSpecError, ValueError) as exc:
            return f"오류: media_spec 검증 실패 — {exc}"
        return "media_spec 검증 통과"

    guidance = f"\n참고 플레이북 지침: {playbook_guidance}" if playbook_guidance else ""
    # 근거 노트 — 대본이 인용할 수 있는 유일한 사실 공급원(규칙 4가 참조한다).
    research_block = f"\n근거 자료:\n{research.strip()}\n" if research and research.strip() else ""
    avoid_block = (
        "\n이미 만든 콘텐츠 목록: "
        + " / ".join(avoid_titles)
        + "\n이 목록과 소재·레시피·결론이 겹치면 안 된다 — 같은 분야라도 완전히 다른"
        " 소재(다른 재료·다른 문제·다른 상황)를 골라라.\n"
        if avoid_titles
        else ""
    )
    # JSON 예시의 중괄호 때문에 str.format은 못 쓴다 — 치환은 이 한 자리뿐이다.
    system_prompt = _SYSTEM_PROMPT.replace("{audience}", audience or DEFAULT_AUDIENCE)
    agent = create_deep_agent(
        model=model,
        tools=[set_hook, set_media_spec],
        system_prompt=system_prompt,
    )
    state = agent.invoke(
        {
            "messages": [
                HumanMessage(
                    f"주제: {topic.title} [{topic.category}] — {topic.summary}\n"
                    f"포맷: {content_format}.{guidance}\n{research_block}{avoid_block}"
                    "set_hook과 set_media_spec을 호출한 뒤 마지막 메시지로 본문을 써라."
                )
            ]
        }
    )
    body = _message_text(state["messages"][-1].content)

    if "hook" not in captured:
        raise ContentRejected("set_hook 미호출 — 훅 패턴 미확정", body)
    if "spec" not in captured:
        raise ContentRejected("set_media_spec 미확정 — 유효한 media_spec 없음", body)

    return ContentResult(
        body=body,
        hook_pattern=_as_hook(str(captured["hook"])),
        media_spec=captured["spec"],  # type: ignore[arg-type]
    )


def _as_hook(value: str) -> HookPattern:
    assert value in HOOK_PATTERNS
    return value  # type: ignore[return-value]
