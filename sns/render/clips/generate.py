"""생성 클립 어댑터 — 주제 한 줄로 **움직이는 배경 영상**을 만든다 (팀 4방향 중 ①).

[sns.render.images.generate]의 클립판이다. 정지 이미지 + 줌(motion 템플릿) 대신
영상 모델(Veo)이 컷 배경 클립 자체를 만든다. 기존 영상 제작 트랙과 **별개 기능**이다 —
스타일 `"clip"`([sns.render.video.clip])이 이 클립을 소비하고, 독립 진입점은
scripts/preview_clip_video.py다.

**유료 전용이다.** Veo는 초당 과금(2026-08 기준 lite $0.05/s, fast $0.15/s,
standard $0.40/s — 8초 클립 한 개 $0.40~$3.20)이라 기본 모델은 가장 싼 lite로 두고,
비교는 env로 갈아 끼운다:

    CLIP_GEN_MODEL=veo-3.1-lite-generate-preview   (기본)
    CLIP_GEN_MODEL=veo-3.1-fast-generate-preview

**long-running 작업이다.** 이미지와 달리 응답이 바로 오지 않는다 — 작업을 걸고
(operation name), 몇 초 간격으로 폴링해 완료되면 파일 URI에서 바이트를 받는다.
셋 다 순수 함수(build/parse)로 쪼개 네트워크 없이 테스트한다.

게이트는 이미지와 같은 [sns.render.images.gate.screen_query] — 금지 소재를 요청
전에(과금 전에) 끊는다.
"""

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence

from sns.agents.models import ENV_GEMINI_API_KEY
from sns.net.http import DEFAULT_OPENER, Opener, fetch_bytes
from sns.render.images.gate import screen_query

ENV_CLIP_MODEL = "CLIP_GEN_MODEL"
DEFAULT_MODEL = "veo-3.1-lite-generate-preview"  # 클립 모델 중 최저가(720p)
_BASE = "https://generativelanguage.googleapis.com/v1beta"
MAX_CLIP_BYTES = 80_000_000  # 8초 720p 실측 수 MB — 여유를 두되 메모리 폭탄은 막는다
REQUEST_TIMEOUT_S = 60.0
POLL_INTERVAL_S = 5.0
POLL_TIMEOUT_S = 360.0  # 실측 1~3분 — 넉넉히

# 세로 쇼츠 배경용 고정 지시 — 화풍이 코드에 있는 이유는 이미지와 같다(모델 비교 오염 방지).
STYLE_RULES: Sequence[str] = (
    "vertical 9:16 video",
    "gentle slow camera motion",
    "clean bright scene",
    "no text, no captions, no letters, no watermark, no logos",
)


class ClipGenerationError(RuntimeError):
    """클립 생성 실패 — 설정 누락·게이트·폴링 타임아웃·응답 이상 포함."""


def build_prompt(subject: str, style_rules: Sequence[str] = STYLE_RULES) -> str:
    return f"{subject.strip()}. " + ", ".join(style_rules) + "."


def resolve_model(model: str | None) -> str:
    return (model or os.environ.get(ENV_CLIP_MODEL) or DEFAULT_MODEL).strip()


def _load_json(payload: bytes) -> Mapping[str, object]:
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise ClipGenerationError(f"응답 JSON 파싱 실패: {exc}") from exc
    if not isinstance(data, dict):
        raise ClipGenerationError(f"응답이 객체가 아님: {str(data)[:120]}")
    return data


def start_request(model: str, prompt: str, api_key: str) -> urllib.request.Request:
    body = {"instances": [{"prompt": prompt}], "parameters": {"aspectRatio": "9:16"}}
    return urllib.request.Request(
        f"{_BASE}/models/{model}:predictLongRunning",
        data=json.dumps(body).encode(),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
    )


def parse_operation(payload: bytes) -> str:
    """작업 시작 응답 → operation name."""
    name = _load_json(payload).get("name")
    if not isinstance(name, str) or not name.strip():
        raise ClipGenerationError(f"응답에 operation name이 없음: {payload[:120]!r}")
    return name


def poll_request(operation: str, api_key: str) -> urllib.request.Request:
    return urllib.request.Request(f"{_BASE}/{operation}", headers={"x-goog-api-key": api_key})


def parse_poll(payload: bytes) -> str | None:
    """폴링 응답 → 완료면 영상 파일 URI, 진행 중이면 None."""
    data = _load_json(payload)
    if not data.get("done"):
        return None
    error = data.get("error")
    if isinstance(error, Mapping):
        raise ClipGenerationError(f"클립 생성 작업 실패: {error.get('message', error)}")
    try:
        response = data["response"]["generateVideoResponse"]  # type: ignore[index]
        # SDK 세대에 따라 키가 다르다 — 실측 전이라 둘 다 받는다.
        samples = response.get("generatedSamples") or response.get("generatedVideos")
        uri = samples[0]["video"]["uri"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ClipGenerationError(f"완료 응답 형식이 예상과 다름: {exc}") from exc
    if not isinstance(uri, str) or not uri.strip():
        raise ClipGenerationError("완료 응답에 영상 URI가 없음")
    return uri


def download_request(uri: str, api_key: str) -> urllib.request.Request:
    return urllib.request.Request(uri, headers={"x-goog-api-key": api_key})


def generate_clip(
    subject: str,
    *,
    model: str | None = None,
    opener: Opener = DEFAULT_OPENER,
    style_rules: Sequence[str] = STYLE_RULES,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """주제 → mp4 클립 바이트. 게이트를 **요청 전에** 통과해야 한다(유료 호출 방어)."""
    model_name = resolve_model(model)
    verdict = screen_query(subject)
    if not verdict.allowed:
        raise ClipGenerationError(f"생성 주제가 게이트에 막힘 — {verdict.reason}")
    api_key = os.environ.get(ENV_GEMINI_API_KEY)
    if not api_key:
        raise ClipGenerationError(
            f"env {ENV_GEMINI_API_KEY}가 없습니다 — aistudio.google.com/apikey"
        )

    def call(request: urllib.request.Request, what: str, max_bytes: int) -> bytes:
        try:
            return fetch_bytes(
                request, timeout_s=REQUEST_TIMEOUT_S, opener=opener, max_bytes=max_bytes
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise ClipGenerationError(
                    "클립 생성 할당량/크레딧 부족(429) — Veo는 유료 전용입니다: "
                    "https://ai.google.dev/gemini-api/docs/pricing"
                ) from exc
            raise ClipGenerationError(f"{what} 호출 실패: HTTP {exc.code}") from exc
        except OSError as exc:
            raise ClipGenerationError(f"{what} 호출 실패: {exc}") from exc

    prompt = build_prompt(subject, style_rules)
    operation = parse_operation(
        call(start_request(model_name, prompt, api_key), "클립 생성 시작", 1_000_000)
    )

    waited = 0.0
    while True:
        uri = parse_poll(call(poll_request(operation, api_key), "클립 생성 폴링", 1_000_000))
        if uri is not None:
            break
        if waited >= POLL_TIMEOUT_S:
            raise ClipGenerationError(
                f"클립 생성 폴링 타임아웃({POLL_TIMEOUT_S:.0f}s) — {operation}"
            )
        sleep(POLL_INTERVAL_S)
        waited += POLL_INTERVAL_S

    data = call(download_request(uri, api_key), "클립 다운로드", MAX_CLIP_BYTES)
    if not data:
        raise ClipGenerationError("다운로드된 클립이 비어 있음")
    return data
