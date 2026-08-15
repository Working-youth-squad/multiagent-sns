"""Content 에이전트 — 훅 분리·media_spec 검증·본문 착지·결정론. 네트워크 0."""

import json
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from sns.agents.content import ContentRejected, run_content
from sns.agents.topic import TopicResult
from sns.tools.contracts import ContentFormat

_TOPIC = TopicResult(
    title="파이썬 walrus 연산자",
    category="기초지식",
    source="github_trending",
    summary=":= 로 대입과 조건을 한 줄에",
    reason="trend=github_trending",
)

_CARD_SPEC = {
    "hook": "이거 3초컷",
    "title": "walrus",
    "body": ["a := 10", "if a > 5:"],
    "footer": "팔로우",
}
_VIDEO_SPEC = {
    "slides": [{"title": "walrus", "body": "한 줄 대입", "narration": "왈러스 연산자입니다"}]
}


class ScriptedChatModel(GenericFakeChatModel):
    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self


def _tool(name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "c1"}])


def _full_script(
    spec: dict[str, object], *, hook: str = "curiosity", body: str = "본문 #개발"
) -> list[AIMessage]:
    return [
        _tool("set_hook", {"pattern": hook}),
        _tool("set_media_spec", {"spec_json": json.dumps(spec, ensure_ascii=False)}),
        AIMessage(content=body),
    ]


def _run(
    script: list[AIMessage], *, fmt: ContentFormat = "feed_image", guidance: str | None = None
) -> Any:
    return run_content(
        ScriptedChatModel(messages=iter(script)),
        topic=_TOPIC,
        content_format=fmt,
        playbook_guidance=guidance,
    )


def test_card_content_ok() -> None:
    result = _run(_full_script(_CARD_SPEC))
    assert result.body == "본문 #개발"
    assert result.hook_pattern == "curiosity"
    assert result.media_spec == _CARD_SPEC


def test_video_content_ok() -> None:
    result = _run(_full_script(_VIDEO_SPEC), fmt="shorts")
    assert result.hook_pattern == "curiosity"
    assert result.media_spec["slides"]  # type: ignore[index]


def test_deterministic_replay() -> None:
    assert _run(_full_script(_CARD_SPEC)) == _run(_full_script(_CARD_SPEC))


def test_invalid_spec_then_valid() -> None:
    # 잘못된 spec(footer 누락)은 오류를 돌려주고, 이어진 유효 호출이 확정된다.
    bad = {"hook": "h", "title": "t", "body": ["x"]}  # footer 없음 → CardSpecError
    script = [
        _tool("set_hook", {"pattern": "question"}),
        _tool("set_media_spec", {"spec_json": json.dumps(bad, ensure_ascii=False)}),
        _tool("set_media_spec", {"spec_json": json.dumps(_CARD_SPEC, ensure_ascii=False)}),
        AIMessage(content="본문"),
    ]
    result = _run(script)
    assert result.media_spec == _CARD_SPEC
    assert result.hook_pattern == "question"


def test_missing_hook_rejected() -> None:
    script = [
        _tool("set_media_spec", {"spec_json": json.dumps(_CARD_SPEC, ensure_ascii=False)}),
        AIMessage(content="본문"),
    ]
    with pytest.raises(ContentRejected):
        _run(script)


def test_missing_spec_rejected() -> None:
    script = [_tool("set_hook", {"pattern": "story"}), AIMessage(content="본문")]
    with pytest.raises(ContentRejected):
        _run(script)


def test_invalid_hook_pattern_not_confirmed() -> None:
    # 잘못된 패턴은 기록 안 됨 → 훅 미확정 → 거부.
    script = [
        _tool("set_hook", {"pattern": "clickbait"}),
        _tool("set_media_spec", {"spec_json": json.dumps(_CARD_SPEC, ensure_ascii=False)}),
        AIMessage(content="본문"),
    ]
    with pytest.raises(ContentRejected):
        _run(script)


def test_video_spec_rejected_for_card_format() -> None:
    # 영상 spec을 피드 포맷에 주면 카드 파서가 막는다(hook/title/footer 누락).
    script = [
        _tool("set_hook", {"pattern": "shock"}),
        _tool("set_media_spec", {"spec_json": json.dumps(_VIDEO_SPEC, ensure_ascii=False)}),
        AIMessage(content="본문"),
    ]
    with pytest.raises(ContentRejected):
        _run(script)
