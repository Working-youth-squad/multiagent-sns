"""생성 이미지 어댑터 (Gemini) — 주제에 맞는 그림을 **만들어서** 정사각에 넣는다.

스톡의 한계에서 나온 트랙이다. 실사 스톡은 검색어의 단어에만 반응해 개념과 무관한
사진을 물어온다("list vs set" → 전선 사진). 생성은 원하는 구도를 직접 말할 수 있다.

**무료 티어 할당량이 0이라 결제를 켜야만 동작한다.** 2026-08 실측: 이미지 모델 7종
전부 `GenerateRequestsPerDayPerProjectPerModel-FreeTier` 가 0이라 429가 돌아온다.
그래서 이 모듈은 기본 배선에서 빠져 있다 — [sns.render.images.resolve]에 `generate`를
주입해야만 돈다. 429는 오류가 아니라 **기본 상태**라, 사유를 사람이 읽을 수 있게 바꿔준다.

화풍은 코드가 고정한다(`STYLE_RULES`). 컷마다 화풍이 튀면 한 영상 안에서 판이 바뀐 것처럼
보이는데, 이건 LLM에게 맡길 판단이 아니다. **글자는 넣지 않게 한다** — 생성 모델의 글자는
뭉개지고, 글자는 우리가 그린다([sns.render.concept_image]).
"""

import base64
import json
import os
import urllib.error
import urllib.request
from collections.abc import Sequence

from sns.agents.models import ENV_GEMINI_API_KEY
from sns.net.http import DEFAULT_OPENER, Opener, fetch_bytes
from sns.render.images.gate import screen_query

# 이미지 모델 중 가장 싼 쪽. 정사각 일러스트에는 상위 모델의 여력이 필요 없다.
IMAGE_MODEL = "gemini-3.1-flash-lite-image"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_IMAGE_BYTES = 12_000_000
TIMEOUT_S = 120.0  # 이미지 생성은 텍스트보다 훨씬 느리다

# 우리 화면에 맞는 화풍 — 940 정사각, 어두운 배경, 글자 없음.
# 비율은 API 파라미터가 아니라 프롬프트로 말한다. 모르는 필드를 보내면 400이 나는데,
# 결제를 켜기 전에는 그걸 확인할 방법이 없다. 프롬프트는 틀려도 그림만 덜 맞을 뿐이다
# (정사각이 아니어도 [sns.render.images.square]가 가운데를 잘라낸다).
STYLE_RULES: Sequence[str] = (
    "square 1:1 composition",
    "flat vector illustration",
    "dark background #0d1117",
    "muted blue and grey palette with one accent",
    "minimal, no text, no letters, no numbers, no watermark, no logos",
)


class ImageGenerationError(RuntimeError):
    """이미지 생성 실패 — 설정 누락·할당량·응답 이상 포함."""


def build_prompt(subject: str) -> str:
    """주제 한 줄 + 고정 화풍. 화풍이 코드에 있는 게 이 함수의 요점이다."""
    return f"{subject.strip()}. " + ", ".join(STYLE_RULES) + "."


def parse_image_response(payload: bytes) -> bytes:
    """generateContent 응답 → 이미지 바이트. 네트워크와 분리된 순수 함수."""
    try:
        data = json.loads(payload)
        parts = data["candidates"][0]["content"]["parts"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ImageGenerationError(f"응답 형식이 예상과 다름: {exc}") from exc

    for part in parts:
        # 모델이 설명 문장을 같이 얹어 보내는 경우가 있어 이미지 파트만 고른다.
        blob = part.get("inlineData") if isinstance(part, dict) else None
        if not isinstance(blob, dict):
            continue
        mime = str(blob.get("mimeType", ""))
        if not mime.startswith("image/"):
            raise ImageGenerationError(f"이미지가 아닌 mime: {mime!r}")
        try:
            raw = base64.b64decode(str(blob.get("data", "")), validate=True)
        except (ValueError, TypeError) as exc:
            raise ImageGenerationError(f"이미지 base64 디코드 실패: {exc}") from exc
        if len(raw) > MAX_IMAGE_BYTES:
            raise ImageGenerationError(f"이미지 크기가 상한({MAX_IMAGE_BYTES:,}바이트)을 넘음")
        return raw
    raise ImageGenerationError("응답에 이미지 파트가 없음 — 모델이 텍스트만 돌려줬다")


def generate_image(
    subject: str, *, model: str = IMAGE_MODEL, opener: Opener = DEFAULT_OPENER
) -> bytes:
    """주제 → 이미지 바이트. 게이트를 **요청 전에** 통과해야 한다(FR-Q7, 유료 호출 방어)."""
    verdict = screen_query(subject)
    if not verdict.allowed:
        raise ImageGenerationError(f"생성 주제가 게이트에 막힘 — {verdict.reason}")
    api_key = os.environ.get(ENV_GEMINI_API_KEY)
    if not api_key:
        raise ImageGenerationError(
            f"env {ENV_GEMINI_API_KEY}가 없습니다 — aistudio.google.com/apikey"
        )

    request = urllib.request.Request(
        f"{API_BASE}/{model}:generateContent",
        data=json.dumps({"contents": [{"parts": [{"text": build_prompt(subject)}]}]}).encode(),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
    )
    try:
        payload = fetch_bytes(
            request, timeout_s=TIMEOUT_S, opener=opener, max_bytes=MAX_IMAGE_BYTES * 2
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise ImageGenerationError(
                "이미지 생성 할당량 초과 — 무료 티어는 이미지 모델 할당량이 0이라 "
                "**결제를 켜야** 동작합니다 (429). https://ai.google.dev/gemini-api/docs/rate-limits"
            ) from exc
        raise ImageGenerationError(f"이미지 생성 호출 실패: HTTP {exc.code}") from exc
    except OSError as exc:
        raise ImageGenerationError(f"이미지 생성 호출 실패: {exc}") from exc
    return parse_image_response(payload)
