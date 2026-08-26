"""키워드 챗봇 **프리뷰** — DB·LLM API 키 없이 눈으로 확인용.

uv run python scripts/preview_chat_web.py   → http://127.0.0.1:8003/

- 저장소: InMemoryChatStore (서버 끄면 사라짐 — 실 운영은 run_chat_web.py)
- LLM: `EchoSearchModel` — 사용자 발화를 그대로 질의어로 삼아 `search_keywords`를 한 번
  부르고 정해진 문장으로 답한다. **대화 지능은 없다**(실 서비스는 Gemini).
- 키워드 조회: **진짜다.** 자동완성 3소스는 전부 무인증이라 키 없이 실제로 긁는다 —
  표에 뜨는 수치는 실제 관측값이다(네트워크는 필요하다).
- 콘텐츠 제작: 미배선(`start_cycle_fn=None`) — 확정해도 초안은 만들지 않는다.

**프리뷰의 목적은 표시 규율 확인이다**: rank_std 미정의 칸, filter_mode 3값 문장,
unscored 부분집합 문구, 소스 실패 노출이 실제 데이터에서 어떻게 보이는지.
"""

import sys
from collections.abc import Callable, Sequence
from typing import Any

import uvicorn
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from sns.chat.store import InMemoryChatStore
from sns.web.chat.app import create_app

_REPLY = (
    "위 표가 3소스에서 실제로 관측한 결과입니다. "
    "(프리뷰라 해설은 고정 문장입니다 — 실 서비스에서는 LLM이 여기서 후보를 좁혀줍니다.)"
)


class EchoSearchModel(BaseChatModel):
    """마지막 사용자 발화를 질의어로 `search_keywords`를 1회 부르고 끝내는 최소 모델.

    툴 결과(ToolMessage)가 이미 대화에 있으면 더 부르지 않고 답문으로 마무리한다 —
    그게 deepagents 루프의 종료 조건이다.
    """

    @property
    def _llm_type(self) -> str:
        return "preview-echo-search"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, BaseMessage]:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        already_searched = any(isinstance(m, ToolMessage) for m in messages)
        if already_searched:
            reply: BaseMessage = AIMessage(content=_REPLY)
        else:
            query = next(
                (str(m.content) for m in reversed(messages) if isinstance(m, HumanMessage)), ""
            )
            reply = AIMessage(
                content="",
                tool_calls=[
                    {"name": "search_keywords", "args": {"query": query}, "id": "preview-1"}
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=reply)])


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    app = create_app(InMemoryChatStore(), model=EchoSearchModel())
    print("키워드 챗봇 프리뷰: http://127.0.0.1:8003/")
    print("  · 키워드 조회는 실제 3소스를 긁습니다(무인증). 네트워크가 필요합니다.")
    print("  · 대화 지능·콘텐츠 제작은 없습니다 — 실 운영은 run_chat_web.py")
    uvicorn.run(app, host="127.0.0.1", port=8003)
    return 0


if __name__ == "__main__":
    sys.exit(main())
