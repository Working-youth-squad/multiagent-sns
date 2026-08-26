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
from sns.tools.contracts import ContentFormat, VideoMethod
from sns.topic_policy import (
    concept_examples_for,
    concept_kinds_for,
    square_guidance_for,
    subject_label_for,
)

# 훅 패턴 5종 (FR-G5 · content_item.hook_pattern CHECK).
HookPattern = Literal["bold_claim", "curiosity", "story", "shock", "question"]
HOOK_PATTERNS: tuple[str, ...] = get_args(HookPattern)

_PROMPT_HEAD = """당신은 SNS 성장 엔진의 Content 에이전트다. \
주어진 «DOMAIN» 주제로 포맷에 맞는 콘텐츠를 만든다.

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
     {"topic","slides":[{"subtitle","narration", ...정사각 필드}...]}
     화면은 3단이다 — 위=주제·부제, 가운데=정사각, 아래=자막(=나레이션).
     * topic: 영상 내내 화면 위에 고정되는 주제 한 줄. **한글 22자 이내.**
       시청자가 중간부터 봐도 무슨 영상인지 알게 하는 앵커다.
     * **슬라이드 1장 = 화면 1장.** 슬라이드마다 부제·정사각·자막이 동시에 바뀐다.
     * subtitle: 이 컷이 무엇을 다루는지 알약에 들어갈 짧은 말. **한글 20자 이내.**
       topic을 되풀이하지 말고 흐름을 진행시켜라("왜 느린가" → "해법" → "결과").
     * narration: TTS 발화이자 하단 자막. **한글 31자 이내 한 문장.** 길면 그 화면이
       4초 넘게 멈춰 있어 거부된다. 슬라이드 수는 넉넉하니(최대 60장), 길게 설명할
       내용은 짧은 슬라이드 여러 장으로 나눠라(정보량을 줄이지 말 것).
"""

_PROMPT_TAIL = """4. **근거를 댈 수 없는 구체 수치를 쓰지 않는다.**
   "확 빨라집니다"·"이거 모르면 손해" 같은 과장은 좋지만,
   "생산성 300% 향상"·"3배 빨라짐"처럼 측정하지 않은 숫자는 독자에게 신뢰를 잃는다.
   근거가 있으면 근거와 함께 쓰고(예: "십만 건 기준 비교 횟수 1/10만"),
   없으면 숫자를 빼고 말로 쓴다.
5. 마지막 메시지는 발행 캡션/본문(body)이다 — 해시태그 포함 가능, 한국어로 쓴다.
   캡션은 마크다운을 렌더하지 않는 플랫폼에 올라가니 **, ##, --- 같은 기호를 쓰지 않는다.
6. 정치·특정인 비방·음란·불법 복제 소재는 쓰지 않는다 — 게이트에 걸려 발행이 막힌다.

영상 포맷(reels/shorts)이면 **set_plan을 가장 먼저** 불러 제작 방식을 확정한다.
«METHODS»

그다음 set_hook과 set_media_spec을 호출하고 마지막 메시지로 본문을 마무리한다."""


# 제작 방식 안내 — 배선된 조합마다 무엇을 쓰라고 말할지. 목록에 없는 조합은 이름만 나열한다.
_METHOD_GUIDE: dict[tuple[str, ...], str] = {
    ("template",): "가능한 방식: template(코드·개념 그림·사진으로 3단 화면을 만든다) 하나뿐이다.",
    ("template", "generated_scene"): """가능한 방식 둘:
  template        — 코드·개념 그림·사진으로 3단 화면. 보여줄 코드나 도해가 있을 때.
  generated_scene — 컷마다 장면을 그려 화면 전체를 채운다. 분위기·이야기가 중요할 때.
  generated_scene을 고르면 슬라이드마다 scene_prompt(영문 한 문장, 무엇이 어떻게
  놓여 있는지)를 쓴다. 화풍은 코드가 붙이니 구도만 써라. 글자는 넣지 마라.""",
}


def _system_prompt(topic_major: str, supported_methods: Sequence[str] = ("template",)) -> str:
    """주제 대분류 → Content 시스템 프롬프트.

    대분류에서 오는 건 셋이다 — 주제 범위 문구, **정사각 섹션 전체**, 개념 그림 예시.
    정사각을 무엇으로 채우는지가 주제마다 가장 크게 갈린다(개발은 코드 스니펫이
    1순위지만 코드가 없는 주제엔 그 안내가 잡음이다). 그림꼴의 *구조*는
    [sns.render.concept_image]에 남고 여기엔 **무엇을 넣으라고 말할지**만 온다.
    파생은 [sns.topic_policy]가 한다.

    프롬프트에 JSON 중괄호가 많아 f-string을 쓰지 않는다 — 전부 이스케이프해야 해서
    읽기 어려워진다. 마커 치환이 원문을 그대로 두므로 프롬프트를 고칠 때 안전하다.
    """
    kinds = concept_kinds_for(topic_major)
    examples_by_kind = concept_examples_for(topic_major)
    examples = "\n".join(examples_by_kind[k] for k in kinds)
    square = (
        square_guidance_for(topic_major)
        .replace("«EXAMPLES»", examples)
        .replace("«N»", str(len(kinds)))
    )
    head = _PROMPT_HEAD.replace("«DOMAIN»", subject_label_for(topic_major))
    # 후보를 프롬프트에 실어도 **자물쇠는 set_plan이 건다** — 프롬프트에서 안내를 빼는
    # 것만으로는 부족하다(에이전트가 안내를 무시하면 그대로 통과한다).
    methods = _METHOD_GUIDE.get(tuple(supported_methods), f"가능한 방식: {list(supported_methods)}")
    return head + square + "\n" + _PROMPT_TAIL.replace("«METHODS»", methods)


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


def _plan_error(method: str, supported: Sequence[str]) -> str | None:
    """고른 제작 방식이 이 환경에서 가능한가. 통과면 None.

    **렌더 시점이 아니라 여기서 막는다.** 라우터가 사후 거부하면 대본을 다 만든 뒤에
    터져 토큰과 시간을 이미 태운 뒤다. 여기서 거부하면 LLM이 즉시 고쳐 재호출한다.
    """
    if method not in supported:
        return f"오류: 이 환경이 못 하는 제작 방식 {method!r} (가능: {list(supported)})"
    return None


def _validate_spec(spec: object, fmt: ContentFormat, topic_major: str) -> dict[str, object]:
    """포맷별 기존 파서로 media_spec 검증. 통과한 dict를 그대로 반환(jsonb 저장용).

    영상 파서에는 주제 대분류를 넘긴다 — 프롬프트가 열어준 개념 그림과 파서가 받는
    종류가 어긋나면 에이전트가 고칠 수 없는 오류를 반복한다.
    """
    if not isinstance(spec, dict):
        raise ValueError("media_spec은 객체여야 함")
    if fmt == "feed_image":
        parse_card_spec(spec)  # 던지면 CardSpecError
    elif fmt in ("reels", "shorts"):
        # PLAN 단계 — 해소 전이라 scene_prompt가 필수이고 scene_ref는 금지다
        # (LLM이 저장소 URL을 환각으로 써넣는 경로를 막는다).
        parse_video_spec(spec, topic_major=topic_major, stage="plan")  # 던지면 VideoSpecError
    else:
        raise ValueError(f"미지원 포맷: {fmt}")
    return spec


def run_content(
    model: BaseChatModel,
    *,
    topic: TopicResult,
    content_format: ContentFormat,
    playbook_guidance: str | None = None,
    topic_major: str,
    supported_methods: Sequence[VideoMethod] = ("template",),
) -> ContentResult:
    """주제 → 포맷별 콘텐츠. 훅·검증된 media_spec·본문 모두 확보돼야 반환.

    `topic_major`는 주제 범위와 쓸 수 있는 개념 그림을 정한다([sns.topic_policy]).
    **기본값이 없다** — 빠뜨리면 요리 채널에 개발 규칙이 조용히 적용되고, 그 사고는
    렌더가 끝난 뒤에야 드러난다.

    `supported_methods`는 **이 실행 환경이 배선한 렌더러 목록**이다
    ([sns.render.video.router]). 기본값이 template 하나뿐이라, 생성 트랙은 진입점이
    명시로 열어야 켜진다 — 비싼 방식이 조용히 기본이 되지 않게.
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
    def set_plan(video_method: str) -> str:
        """이 영상의 제작 방식을 확정한다. set_media_spec보다 먼저 호출해야 한다."""
        error = _plan_error(video_method, supported_methods)
        if error:
            return error
        captured["plan"] = video_method
        return f"제작 방식 확정: {video_method}"

    @tool
    def set_media_spec(spec_json: str) -> str:
        """포맷에 맞는 media_spec(JSON 문자열)을 검증·기록한다. 틀리면 오류 사유를 돌려준다."""
        # 순서 강제 — 실행 방식을 몰래 정하지 못하게 한다.
        if content_format in ("reels", "shorts") and "plan" not in captured:
            return "오류: set_plan을 먼저 호출해야 한다"
        try:
            spec = json.loads(spec_json)
        except (TypeError, ValueError) as exc:
            return f"오류: JSON 파싱 실패 — {exc}"
        try:
            # method는 **코드가 찍는다** — LLM이 media_spec에 또 쓰게 하면 진실이
            # 둘이 되고, 두 값이 어긋나는 실패 모드가 새로 생긴다.
            if isinstance(spec, dict) and "plan" in captured:
                spec = {**spec, "method": captured["plan"]}
            captured["spec"] = _validate_spec(spec, content_format, topic_major)
        except (CardSpecError, VideoSpecError, ValueError) as exc:
            return f"오류: media_spec 검증 실패 — {exc}"
        return "media_spec 검증 통과"

    guidance = f"\n참고 플레이북 지침: {playbook_guidance}" if playbook_guidance else ""
    agent = create_deep_agent(
        model=model,
        tools=[set_plan, set_hook, set_media_spec],
        system_prompt=_system_prompt(topic_major, supported_methods),
    )
    state = agent.invoke(
        {
            "messages": [
                HumanMessage(
                    f"주제: {topic.title} [{topic.category}] — {topic.summary}\n"
                    f"포맷: {content_format}.{guidance}\n"
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
