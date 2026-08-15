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

_SYSTEM_PROMPT = """당신은 SNS 성장 엔진의 Content 에이전트다. \
주어진 개발자 주제로 포맷에 맞는 콘텐츠를 만든다.

규칙:
1. 첫 화면(훅)과 본문을 분리한다. 훅은 스크롤을 멈출 이유를 첫 프레임/첫 카드에 담는다.
2. 훅 패턴을 하나 정해 set_hook으로 기록한다:
   bold_claim(단언)·curiosity(호기심)·story(이야기)·shock(충격)·question(질문).
3. media_spec을 set_media_spec에 JSON으로 넘긴다. 틀리면 오류가 돌아오니 고쳐 다시 호출한다.
   - 피드(feed_image): {"hook","title","body":[단락...],"footer"}
   - 릴스/쇼츠(reels/shorts): {"slides":[{"title","body","narration"}...]}
     (장면마다 화면 텍스트와 나레이션 분리)
4. 마지막 메시지는 발행 캡션/본문(body)이다 — 해시태그 포함 가능, 한국어로 쓴다.

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
    else:  # reels · shorts
        parse_video_spec(spec)  # 던지면 VideoSpecError
    return spec


def run_content(
    model: BaseChatModel,
    *,
    topic: TopicResult,
    content_format: ContentFormat,
    playbook_guidance: str | None = None,
) -> ContentResult:
    """주제 → 포맷별 콘텐츠. 훅·검증된 media_spec·본문 모두 확보돼야 반환."""
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
    agent = create_deep_agent(
        model=model,
        tools=[set_hook, set_media_spec],
        system_prompt=_SYSTEM_PROMPT,
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
