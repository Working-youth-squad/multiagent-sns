"""LLM 모델 팩토리 — 모델명을 env로 고른다. 네트워크 0.

Gemini 무료 티어 쿼터는 **모델별**이다(`GenerateRequestsPerDayPerProjectPerModel`).
gemini-3.5-flash가 하루 20건을 소진해도 flash-lite는 따로 남아 있어, 모델을 바꾸면
그날 작업을 이어갈 수 있다. 코드를 고쳐야만 바꿀 수 있으면 그 순간 막힌다.
"""

import pytest

from sns.agents.models import DEFAULT_GEMINI_MODEL, ENV_GEMINI_MODEL, resolve_model_name


def test_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_GEMINI_MODEL, raising=False)
    assert resolve_model_name() == DEFAULT_GEMINI_MODEL


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_GEMINI_MODEL, "gemini-3.5-flash-lite")
    assert resolve_model_name() == "gemini-3.5-flash-lite"


def test_blank_env_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """빈 값은 '설정 안 함'이다 — .env에 키만 두고 값을 비워두는 일이 흔하다."""
    monkeypatch.setenv(ENV_GEMINI_MODEL, "   ")
    assert resolve_model_name() == DEFAULT_GEMINI_MODEL
