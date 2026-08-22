"""LLM 모델 팩토리 — 자격증명 규약.

트렌드 리서치(C1)의 LLM 그라운딩이 `GEMINI_API_KEY`를 쓰는데 에이전트는
`GOOGLE_API_KEY`를 써서, 하나만 설정하면 다른 쪽이 조용히 죽었다. 이름을 하나로 모은다.
"""

import pytest

from sns.agents.models import ENV_GEMINI_API_KEY, make_model


def test_env_name_matches_trend_research() -> None:
    """C1 트렌드 리서치와 같은 env 이름을 써야 키 하나로 둘 다 산다."""
    from sns.research.trends import ENV_GEMINI_API_KEY as TRENDS_ENV

    assert ENV_GEMINI_API_KEY == TRENDS_ENV == "GEMINI_API_KEY"


def test_missing_key_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_GEMINI_API_KEY, raising=False)
    with pytest.raises(RuntimeError, match=ENV_GEMINI_API_KEY):
        make_model()
