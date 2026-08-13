"""LLM 모델 팩토리 — 주입 seam의 유일한 실물 공급처 (FR-C3).

에이전트는 항상 `BaseChatModel` 인스턴스를 주입받는다(테스트 = ScriptedChatModel).
프로바이더 교체(Gemini → Claude/OpenAI)는 이 함수 본문만 바꾸면 된다 —
호출부·에이전트 코드는 무변경.
"""

import os

from langchain_core.language_models import BaseChatModel

ENV_GOOGLE_API_KEY = "GOOGLE_API_KEY"
GEMINI_MODEL = "gemini-2.5-flash"  # 무료 티어


def make_model() -> BaseChatModel:
    """실물 LLM 생성. API 키는 env `GOOGLE_API_KEY` (aistudio.google.com/apikey)."""
    if not os.environ.get(ENV_GOOGLE_API_KEY):
        raise RuntimeError(f"env {ENV_GOOGLE_API_KEY} 누락 — aistudio.google.com/apikey에서 발급")
    # 지연 임포트: 오프라인 테스트가 프로바이더 패키지 로딩 비용을 지지 않게.
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=GEMINI_MODEL, temperature=0)
