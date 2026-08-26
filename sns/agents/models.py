"""LLM 모델 팩토리 — 주입 seam의 유일한 실물 공급처 (FR-C3).

에이전트는 항상 `BaseChatModel` 인스턴스를 주입받는다(테스트 = ScriptedChatModel).
프로바이더 교체는 **여기서만** 일어난다 — 호출부·에이전트 코드는 무변경.

프로바이더는 env `SNS_MODEL_PROVIDER`로 고른다(기본 `gemini` = 기존 동작 무변경).
키 존재 여부로 자동 판별하지 않는 이유: 두 키가 다 있는 개발 환경에서 어느 쪽이
돌았는지가 조용히 갈린다. 비용이 드는 선택은 명시적이어야 한다.

| 프로바이더 | 키 | 모델 env | 기본 모델 |
|---|---|---|---|
| `gemini` (기본) | `GEMINI_API_KEY` | `GEMINI_MODEL` | `gemini-3.5-flash` |
| `openai` | `OPENAI_API_KEY` | `OPENAI_MODEL` | `gpt-5-nano` |
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

ENV_OPENAI_API_KEY = "OPENAI_API_KEY"
ENV_OPENAI_MODEL = "OPENAI_MODEL"
# 최저가 계층. 툴 호출 2개짜리 얕은 대화(챗봇)에는 충분하지만, 깊은 다단 추론에서는
# 날조가 관측된 계층이기도 하다 — 품질이 모자라면 OPENAI_MODEL로 올린다.
DEFAULT_OPENAI_MODEL = "gpt-5-nano"

ENV_PROVIDER = "SNS_MODEL_PROVIDER"
DEFAULT_PROVIDER = "gemini"
PROVIDERS: tuple[str, ...] = ("gemini", "openai")


def resolve_provider() -> str:
    provider = os.environ.get(ENV_PROVIDER, "").strip().lower() or DEFAULT_PROVIDER
    if provider not in PROVIDERS:
        raise RuntimeError(f"env {ENV_PROVIDER}는 {list(PROVIDERS)} 중 하나여야 한다: {provider!r}")
    return provider


def resolve_model_name(provider: str | None = None) -> str:
    """env로 모델을 고른다 — 무료 티어 쿼터가 **모델별**이기 때문이다.

    `GenerateRequestsPerDayPerProjectPerModel-FreeTier`가 하루 20건이라, flash가
    소진돼도 flash-lite는 따로 남아 있다. 코드를 고쳐야만 모델을 바꿀 수 있으면
    쿼터가 떨어진 그 순간 그날 작업이 멈춘다.
    """
    active = provider or resolve_provider()
    if active == "openai":
        return os.environ.get(ENV_OPENAI_MODEL, "").strip() or DEFAULT_OPENAI_MODEL
    return os.environ.get(ENV_GEMINI_MODEL, "").strip() or DEFAULT_GEMINI_MODEL


def required_key_env(provider: str | None = None) -> str:
    """이 프로바이더가 요구하는 키의 env 이름 — 진입점이 사전 점검에 쓴다."""
    return (
        ENV_OPENAI_API_KEY if (provider or resolve_provider()) == "openai" else ENV_GEMINI_API_KEY
    )


def make_model() -> BaseChatModel:
    """실물 LLM 생성. 프로바이더는 env `SNS_MODEL_PROVIDER`(기본 gemini)."""
    provider = resolve_provider()
    key_env = required_key_env(provider)
    api_key = os.environ.get(key_env)
    if not api_key:
        source = (
            "platform.openai.com/api-keys" if provider == "openai" else "aistudio.google.com/apikey"
        )
        raise RuntimeError(f"env {key_env} 누락 — {source}에서 발급")

    # 지연 임포트: 오프라인 테스트가 프로바이더 패키지 로딩 비용을 지지 않게.
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        # temperature를 넘기지 않는다 — nano/mini 계층은 기본값 외를 거부한다
        # (400 unsupported_value). 결정론은 이 계층에서 애초에 보장되지 않는다.
        return ChatOpenAI(model=resolve_model_name(provider), api_key=api_key)  # type: ignore[arg-type]

    from langchain_google_genai import ChatGoogleGenerativeAI

    # 키를 **명시 전달**한다 — 라이브러리 기본값은 GOOGLE_API_KEY를 읽으므로, 이름을
    # 옮긴 뒤에도 자동 탐색에 의존하면 키를 못 찾는다.
    return ChatGoogleGenerativeAI(
        model=resolve_model_name(provider), temperature=0, google_api_key=api_key
    )
