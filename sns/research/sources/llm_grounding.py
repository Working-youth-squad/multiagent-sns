"""LLM 웹 그라운딩 — Gemini google_search (§2 소스 #6, 선택).

`google_search` 툴로 근거 있는 개발 주제 후보를 받아 줄 단위로 뽑는다. 선택 소스라
`default_service`는 GEMINI_API_KEY가 있을 때만 등록한다(없으면 아예 안 돈다).
"""

import json
import urllib.parse
import urllib.request

from sns.net.http import DEFAULT_OPENER, MAX_RESPONSE_BYTES, Opener

_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
# 이 소스가 쓸 모델. **URL에 박아두지 않는다** — 예전엔 gemini-2.0-flash가 URL에
# 하드코딩돼 있었고, 그 모델이 은퇴하자 404가 나면서 소스가 조용히 죽었다. 소스 격리
# 설계상 실패는 ok=False로 삼켜지므로(FR-G4) 아무도 알아채지 못했다.
# 에이전트의 대화 모델([sns.agents.models])과 일부러 분리한다 — 이쪽은 google_search
# 그라운딩 전용이라 따로 갈아끼울 수 있어야 한다.
DEFAULT_MODEL = "gemini-3.5-flash"


def gemini_url(model: str = DEFAULT_MODEL) -> str:
    return f"{_API_BASE}/{model}:generateContent"


GEMINI_URL = gemini_url()
# 프로필 없는 실험 사이클의 기본 질의. 온보딩 채널은 자기 주제로 만든 질의를 넘긴다
# ([sns.topic_policy.grounding_prompt_for]).
DEFAULT_PROMPT = (
    "한국 개발자 커뮤니티에서 최근 화제인 기술 주제 후보를 근거와 함께 한 줄씩 나열해줘. "
    "각 줄은 '- '로 시작하고, 확인되지 않은 내용은 넣지 마."
)


def parse_llm_grounding(data: bytes) -> tuple[str, ...]:
    """generateContent 응답에서 후보 텍스트를 줄 단위로 뽑는다(불릿 기호 제거)."""
    payload = json.loads(data)
    candidates = payload.get("candidates", [])
    if not candidates:
        return ()
    text = "".join(
        part.get("text", "") for part in candidates[0].get("content", {}).get("parts", [])
    )
    lines = (line.strip().lstrip("-*").strip() for line in text.splitlines())
    return tuple(line for line in lines if line)


def fetch_llm_grounding(
    limit: int,
    *,
    api_key: str,
    prompt: str = DEFAULT_PROMPT,
    url: str = GEMINI_URL,
    timeout_s: float = 10.0,
    opener: Opener = DEFAULT_OPENER,
) -> tuple[str, ...]:
    body = json.dumps(
        {"contents": [{"parts": [{"text": prompt}]}], "tools": [{"google_search": {}}]}
    ).encode("utf-8")
    q = urllib.parse.urlencode({"key": api_key})
    request = urllib.request.Request(
        f"{url}?{q}", data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    with opener(request, timeout=timeout_s) as resp:
        data = resp.read(MAX_RESPONSE_BYTES)
    return parse_llm_grounding(data)[:limit]
