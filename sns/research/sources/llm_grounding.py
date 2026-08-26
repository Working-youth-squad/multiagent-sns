"""LLM 웹 그라운딩 — Gemini google_search (§2 소스 #6, 선택).

`google_search` 툴로 근거 있는 개발 주제 후보를 받아 줄 단위로 뽑는다. 선택 소스라
`default_service`는 GEMINI_API_KEY가 있을 때만 등록한다(없으면 아예 안 돈다).
"""

import json
import urllib.parse
import urllib.request

from sns.net.http import DEFAULT_OPENER, MAX_RESPONSE_BYTES, Opener

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
)
_PROMPT = (
    "한국 개발자 커뮤니티에서 최근 화제인 기술 주제 후보를 근거와 함께 한 줄씩 나열해줘. "
    "각 줄은 '- '로 시작하고, 확인되지 않은 내용은 넣지 마."
)
# 채널 주제 바인딩용(온보딩 프로필 → 후보 풀). 상세 조립(소스 선별·네이버 쿼리 바인딩)은
# 트렌드 담당 몫이고, 여기는 프롬프트 치환이라는 최소 연결만 제공한다.
_PROMPT_TOPIC = (
    "『{topic}』 분야에서 최근 화제인 콘텐츠 주제 후보를 근거와 함께 한 줄씩 나열해줘. "
    "각 줄은 '- '로 시작하고, 확인되지 않은 내용은 넣지 마."
)


def parse_grounded_text(data: bytes) -> str:
    """generateContent 응답 → 본문 텍스트. 리서치([sns.agents.research])도 이걸 쓴다."""
    payload = json.loads(data)
    candidates = payload.get("candidates", [])
    if not candidates:
        return ""
    return "".join(
        part.get("text", "") for part in candidates[0].get("content", {}).get("parts", [])
    )


def _bullet_lines(text: str) -> tuple[str, ...]:
    lines = (line.strip().lstrip("-*").strip() for line in text.splitlines())
    return tuple(line for line in lines if line)


def parse_llm_grounding(data: bytes) -> tuple[str, ...]:
    """generateContent 응답에서 후보 텍스트를 줄 단위로 뽑는다(불릿 기호 제거)."""
    return _bullet_lines(parse_grounded_text(data))


def fetch_grounded_text(
    prompt: str,
    *,
    api_key: str,
    url: str = GEMINI_URL,
    timeout_s: float = 10.0,
    opener: Opener = DEFAULT_OPENER,
) -> str:
    """임의 프롬프트를 google_search 그라운딩으로 1회 실행 — 원문 텍스트를 돌려준다."""
    body = json.dumps(
        {"contents": [{"parts": [{"text": prompt}]}], "tools": [{"google_search": {}}]}
    ).encode("utf-8")
    q = urllib.parse.urlencode({"key": api_key})
    request = urllib.request.Request(
        f"{url}?{q}", data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    with opener(request, timeout=timeout_s) as resp:
        data = resp.read(MAX_RESPONSE_BYTES)
    return parse_grounded_text(data)


def fetch_llm_grounding(
    limit: int,
    *,
    api_key: str,
    topic: str | None = None,
    url: str = GEMINI_URL,
    timeout_s: float = 10.0,
    opener: Opener = DEFAULT_OPENER,
) -> tuple[str, ...]:
    """주제 후보 줄 목록. `topic`이 있으면 그 분야로 묻는다(채널 프로필 최소 바인딩)."""
    prompt = _PROMPT_TOPIC.format(topic=topic.strip()) if topic and topic.strip() else _PROMPT
    text = fetch_grounded_text(
        prompt, api_key=api_key, url=url, timeout_s=timeout_s, opener=opener
    )
    return _bullet_lines(text)[:limit]
