"""LLM 모델 팩토리 — 주입 seam의 유일한 실물 공급처 (FR-C3).

에이전트는 항상 `BaseChatModel` 인스턴스를 주입받는다(테스트 = ScriptedChatModel).
프로바이더 교체(Gemini → Claude/OpenAI)는 이 함수 본문만 바꾸면 된다 —
호출부·에이전트 코드는 무변경.
"""

import os

from langchain_core.language_models import BaseChatModel

# C1 트렌드 리서치의 LLM 그라운딩([sns.research.trends])과 **같은 이름**을 쓴다.
# 예전엔 여기가 GOOGLE_API_KEY, 저기가 GEMINI_API_KEY라 하나만 설정하면 다른 쪽이
# 조용히 죽었다(미등록 소스는 ok=False로 격리돼 에러도 안 났다).
ENV_GEMINI_API_KEY = "GEMINI_API_KEY"
ENV_GEMINI_MODEL = "GEMINI_MODEL"
# 무료 티어 (2.5 세대는 2026년 신규 사용자에게 은퇴됨 — 실측 404).
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"


def resolve_model_name() -> str:
    """env로 모델을 고른다 — 무료 티어 쿼터가 **모델별**이기 때문이다.

    `GenerateRequestsPerDayPerProjectPerModel-FreeTier`가 하루 20건이라, flash가
    소진돼도 flash-lite는 따로 남아 있다. 코드를 고쳐야만 모델을 바꿀 수 있으면
    쿼터가 떨어진 그 순간 그날 작업이 멈춘다.
    """
    return os.environ.get(ENV_GEMINI_MODEL, "").strip() or DEFAULT_GEMINI_MODEL


def make_model() -> BaseChatModel:
    """실물 LLM 생성. API 키는 env `GEMINI_API_KEY` (aistudio.google.com/apikey)."""
    api_key = os.environ.get(ENV_GEMINI_API_KEY)
    if not api_key:
        raise RuntimeError(f"env {ENV_GEMINI_API_KEY} 누락 — aistudio.google.com/apikey에서 발급")
    # 지연 임포트: 오프라인 테스트가 프로바이더 패키지 로딩 비용을 지지 않게.
    from langchain_google_genai import ChatGoogleGenerativeAI

    # 키를 **명시 전달**한다 — 라이브러리 기본값은 GOOGLE_API_KEY를 읽으므로, 이름을
    # 옮긴 뒤에도 자동 탐색에 의존하면 키를 못 찾는다.
    return ChatGoogleGenerativeAI(model=resolve_model_name(), temperature=0, google_api_key=api_key)
