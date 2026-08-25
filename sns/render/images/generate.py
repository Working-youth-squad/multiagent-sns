"""생성 이미지 어댑터 — 주제에 맞는 그림을 **만들어서** 정사각에 넣는다.

스톡의 한계에서 나온 트랙이다. 실사 스톡은 검색어의 단어에만 반응해 개념과 무관한
사진을 물어온다("list vs set" → 전선 사진). 생성은 원하는 구도를 직접 말할 수 있다.

**프로바이더 2종을 env로 갈아 끼운다.** 어느 모델이 우리 화면에 맞는지는 돌려봐야 알고,
그 비교를 코드 수정 없이 하려면 모델이 설정이어야 한다:

    IMAGE_GEN_MODEL=google:gemini-3.1-flash-lite-image
    IMAGE_GEN_MODEL=openai:gpt-image-1

**항상 `provider:model` 형식이다.** 이름만 보고 프로바이더를 추측하는 규칙(gpt-로
시작하면 OpenAI…)은 새 모델이 나올 때마다 깨진다. 명시가 싸다.

**기본 배선에서 빠져 있다** — [sns.render.images.resolve]에 `generate`를 주입해야만 돈다.
이유가 둘이다.

1. 유료다. Google은 2026-08 실측으로 이미지 모델 7종 전부 무료 티어 할당량이 0이고
   (`GenerateRequestsPerDayPerProjectPerModel-FreeTier` = 0), OpenAI 이미지 API도 과금이다.
   429는 오류가 아니라 **기본 상태**라 프로바이더별로 사유를 갈라 안내한다.
2. **코드 영상에서는 개념 그림([sns.render.concept_image])이 이겼다.** 같은 영상에서 컷
   둘만 gpt-image-1로 바꿔 나란히 놓고 골랐다. 화풍은 잘 맞췄지만(어두운 배경·글자 없음),
   코드를 다루는 영상의 핵심 컷은 대개 숫자와 비교였다 — "101번 → 2번"을 개념 그림은
   글자로 쓰지만 생성 이미지는 화살표 개수를 세게 만든다.

그래서 `image_prompt`는 **코드가 한 컷도 없는 영상에서만** 허용된다(규칙은 spec 단위로
[sns.render.video.spec] 안에 있다). 커리어·트렌드·도구 소개처럼 보여줄 코드가 없는
주제가 이 모듈의 자리다. 화풍 비교는 scripts/preview_generated_image.py로 한다.

화풍은 프로바이더와 무관하게 코드가 고정한다(`STYLE_RULES`). 화풍이 갈리면 모델 비교가
화풍 비교로 오염된다. **글자는 넣지 않게 한다** — 생성 모델의 글자는 뭉개지고, 글자는
우리가 그린다([sns.render.concept_image]).
"""

import base64
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from sns.agents.models import ENV_GEMINI_API_KEY
from sns.net.http import DEFAULT_OPENER, Opener, fetch_bytes
from sns.render.images.gate import screen_query

ENV_IMAGE_MODEL = "IMAGE_GEN_MODEL"
ENV_OPENAI_API_KEY = "OPENAI_API_KEY"
# 이미지 모델 중 가장 싼 쪽. 정사각 일러스트에 상위 모델의 여력은 필요 없다.
DEFAULT_MODEL = "google:gemini-3.1-flash-lite-image"
MAX_IMAGE_BYTES = 12_000_000
TIMEOUT_S = 120.0  # 이미지 생성은 텍스트보다 훨씬 느리다

# 우리 화면에 맞는 화풍 — 940 정사각, 어두운 배경, 글자 없음.
STYLE_RULES: Sequence[str] = (
    "square 1:1 composition",
    "flat vector illustration",
    "dark background #0d1117",
    "muted blue and grey palette with one accent",
    "minimal, no text, no letters, no numbers, no watermark, no logos",
)


class ImageGenerationError(RuntimeError):
    """이미지 생성 실패 — 설정 누락·모델 지정 오류·할당량·응답 이상 포함."""


def build_prompt(subject: str) -> str:
    """주제 한 줄 + 고정 화풍. 화풍이 코드에 있는 게 이 함수의 요점이다."""
    return f"{subject.strip()}. " + ", ".join(STYLE_RULES) + "."


def _decode_image(encoded: str) -> bytes:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ImageGenerationError(f"이미지 base64 디코드 실패: {exc}") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageGenerationError(f"이미지 크기가 상한({MAX_IMAGE_BYTES:,}바이트)을 넘음")
    return raw


def _load_json(payload: bytes) -> Mapping[str, object]:
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise ImageGenerationError(f"응답 JSON 파싱 실패: {exc}") from exc
    if not isinstance(data, dict):
        raise ImageGenerationError(f"응답이 객체가 아님: {str(data)[:120]}")
    return data


# ── Google (Gemini generateContent) ───────────────────────────────

_GOOGLE_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _google_request(model: str, prompt: str, api_key: str) -> urllib.request.Request:
    return urllib.request.Request(
        f"{_GOOGLE_BASE}/{model}:generateContent",
        data=json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode(),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
    )


def _google_parse(payload: bytes) -> bytes:
    data = _load_json(payload)
    try:
        parts = data["candidates"][0]["content"]["parts"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise ImageGenerationError(f"응답 형식이 예상과 다름: {exc}") from exc

    for part in parts:
        # 모델이 설명 문장을 같이 얹어 보내는 경우가 있어 이미지 파트만 고른다.
        blob = part.get("inlineData") if isinstance(part, dict) else None
        if not isinstance(blob, dict):
            continue
        mime = str(blob.get("mimeType", ""))
        if not mime.startswith("image/"):
            raise ImageGenerationError(f"이미지가 아닌 mime: {mime!r}")
        return _decode_image(str(blob.get("data", "")))
    raise ImageGenerationError("응답에 이미지 파트가 없음 — 모델이 텍스트만 돌려줬다")


# ── OpenAI (Images API) ───────────────────────────────────────────

_OPENAI_URL = "https://api.openai.com/v1/images/generations"


def _openai_request(model: str, prompt: str, api_key: str) -> urllib.request.Request:
    # 정사각 슬롯이라 1024×1024를 주문한다 — 크롭 손실이 없다.
    body: dict[str, object] = {"model": model, "prompt": prompt, "n": 1, "size": "1024x1024"}
    # gpt-image-1은 response_format을 **받지 않는다**(보내면 400). 항상 b64로 돌려준다.
    # dall-e 계열은 반대로 기본이 URL이라 명시하지 않으면 바이트를 못 받는다.
    if model.startswith("dall-e"):
        body["response_format"] = "b64_json"
    return urllib.request.Request(
        _OPENAI_URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )


def _openai_parse(payload: bytes) -> bytes:
    data = _load_json(payload)
    entries = data.get("data")
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
        raise ImageGenerationError(f"응답에 data 항목이 없음: {str(data)[:120]}")
    first = entries[0]
    if "b64_json" not in first:
        # URL만 오면 2차 요청이 필요한데, spec에서 온 임의 호스트를 때리는 통로를 열지 않는다.
        raise ImageGenerationError(
            "응답에 b64_json이 없음 — URL 반환 모델은 지원하지 않습니다(임의 호스트 다운로드 회피)"
        )
    return _decode_image(str(first["b64_json"]))


# ── 프로바이더 레지스트리 ─────────────────────────────────────────


@dataclass(frozen=True)
class Provider:
    name: str
    env_key: str
    signup_hint: str
    quota_hint: str
    build_request: Callable[[str, str, str], urllib.request.Request]
    parse: Callable[[bytes], bytes]


PROVIDERS: dict[str, Provider] = {
    "google": Provider(
        name="google",
        env_key=ENV_GEMINI_API_KEY,
        signup_hint="aistudio.google.com/apikey",
        quota_hint=(
            "무료 티어는 이미지 모델 할당량이 0이라 **결제를 켜야** 동작합니다 — "
            "https://ai.google.dev/gemini-api/docs/rate-limits"
        ),
        build_request=_google_request,
        parse=_google_parse,
    ),
    "openai": Provider(
        name="openai",
        env_key=ENV_OPENAI_API_KEY,
        signup_hint="platform.openai.com/api-keys",
        quota_hint=(
            "결제·사용량 한도를 확인하세요 — https://platform.openai.com/settings/organization/billing"
        ),
        build_request=_openai_request,
        parse=_openai_parse,
    ),
}


def resolve_model(model: str | None) -> tuple[Provider, str]:
    """`provider:model` → (프로바이더, 모델명). 인자 > env > 기본값 순."""
    raw = (model or os.environ.get(ENV_IMAGE_MODEL) or DEFAULT_MODEL).strip()
    provider_name, _, model_name = raw.partition(":")
    if not model_name.strip():
        raise ImageGenerationError(
            f"{ENV_IMAGE_MODEL}은 'provider:model' 형식이어야 합니다 "
            f"(예: {DEFAULT_MODEL}): 받은 값 {raw!r}"
        )
    provider = PROVIDERS.get(provider_name.strip().lower())
    if provider is None:
        raise ImageGenerationError(
            f"모르는 프로바이더 {provider_name!r} — 가능한 값: {sorted(PROVIDERS)}"
        )
    return provider, model_name.strip()


def generate_image(
    subject: str, *, model: str | None = None, opener: Opener = DEFAULT_OPENER
) -> bytes:
    """주제 → 이미지 바이트. 게이트를 **요청 전에** 통과해야 한다(FR-Q7, 유료 호출 방어)."""
    provider, model_name = resolve_model(model)
    verdict = screen_query(subject)
    if not verdict.allowed:
        raise ImageGenerationError(f"생성 주제가 게이트에 막힘 — {verdict.reason}")
    api_key = os.environ.get(provider.env_key)
    if not api_key:
        raise ImageGenerationError(f"env {provider.env_key}가 없습니다 — {provider.signup_hint}")

    request = provider.build_request(model_name, build_prompt(subject), api_key)
    try:
        payload = fetch_bytes(
            request, timeout_s=TIMEOUT_S, opener=opener, max_bytes=MAX_IMAGE_BYTES * 2
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise ImageGenerationError(
                f"[{provider.name}] 이미지 생성 할당량 초과(429) — {provider.quota_hint}"
            ) from exc
        raise ImageGenerationError(
            f"[{provider.name}] 이미지 생성 호출 실패: HTTP {exc.code}"
        ) from exc
    except OSError as exc:
        raise ImageGenerationError(f"[{provider.name}] 이미지 생성 호출 실패: {exc}") from exc
    return provider.parse(payload)
