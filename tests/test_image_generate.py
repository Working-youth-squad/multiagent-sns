"""생성 이미지 어댑터 — 프로바이더 2종(Google·OpenAI), 네트워크 0(opener 주입).

둘 다 **유료**라 결제 없이는 실제로 안 돈다. 그래서 이 테스트가 검증하는 건 "그림이
예쁜가"가 아니라 그 앞의 계약이다: 모델을 env로 고를 수 있는가, 프로바이더별 요청 모양이
맞는가, FR-Q7 게이트가 요청 전에 도는가, 실패 사유가 사람에게 읽히는가.

모델 지정은 항상 `provider:model`이다. 이름만 보고 프로바이더를 추측하면 새 모델이
나올 때마다 규칙이 깨진다 — 명시가 싸다.
"""

import base64
import io
import json
from contextlib import contextmanager
from typing import Any

import pytest

from sns.agents.models import ENV_GEMINI_API_KEY
from sns.render.images.generate import (
    DEFAULT_MODEL,
    ENV_IMAGE_MODEL,
    ENV_OPENAI_API_KEY,
    MAX_IMAGE_BYTES,
    PROVIDERS,
    STYLE_RULES,
    ImageGenerationError,
    build_prompt,
    generate_image,
    resolve_model,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"fake bytes"
GOOGLE_MODEL = "google:gemini-3.1-flash-lite-image"
OPENAI_MODEL = "openai:gpt-image-1"


def google_body(parts: list[dict[str, Any]]) -> bytes:
    return json.dumps({"candidates": [{"content": {"parts": parts}}]}).encode()


def inline(data: bytes = PNG, mime: str = "image/png") -> dict[str, Any]:
    return {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}


def openai_body(entry: dict[str, Any] | None = None) -> bytes:
    return json.dumps({"data": [entry or {"b64_json": base64.b64encode(PNG).decode()}]}).encode()


def opener_for(payload: bytes, *, seen: list[Any] | None = None) -> Any:
    @contextmanager
    def _open(target: Any, timeout: float = 0) -> Any:
        if seen is not None:
            seen.append(target)
        yield io.BytesIO(payload)

    return _open


@pytest.fixture(autouse=True)
def _no_ambient_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """개발자 .env의 IMAGE_GEN_MODEL이 테스트 결과를 바꾸지 않게."""
    monkeypatch.delenv(ENV_IMAGE_MODEL, raising=False)


# ── 프롬프트 ──────────────────────────────────────────────────────


def test_prompt_carries_the_subject() -> None:
    assert "a single cube apart from a row" in build_prompt("a single cube apart from a row")


def test_prompt_pins_the_house_style() -> None:
    """컷마다 화풍이 튀면 한 영상 안에서 판이 바뀐 것처럼 보인다 — 스타일은 코드가 고정한다."""
    prompt = build_prompt("network of nodes")
    for rule in STYLE_RULES:
        assert rule in prompt


def test_prompt_forbids_text_in_the_image() -> None:
    """생성 모델의 글자는 뭉개진다. 글자는 우리가 그린다(개념 그림)."""
    assert "no text" in build_prompt("x").lower()


def test_prompt_is_provider_independent() -> None:
    """화풍이 프로바이더별로 갈리면 모델 비교가 화풍 비교로 오염된다."""
    assert build_prompt("cube") == build_prompt("cube")


# ── 모델 선택 ─────────────────────────────────────────────────────


def test_env_selects_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_IMAGE_MODEL, OPENAI_MODEL)
    provider, model = resolve_model(None)
    assert (provider.name, model) == ("openai", "gpt-image-1")


def test_explicit_argument_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """스크립트가 여러 모델을 한 번에 비교할 수 있어야 한다 — 인자가 env를 이긴다."""
    monkeypatch.setenv(ENV_IMAGE_MODEL, OPENAI_MODEL)
    provider, model = resolve_model(GOOGLE_MODEL)
    assert (provider.name, model) == ("google", "gemini-3.1-flash-lite-image")


def test_default_when_nothing_set() -> None:
    provider, model = resolve_model(None)
    assert f"{provider.name}:{model}" == DEFAULT_MODEL


def test_unknown_provider_lists_the_valid_ones() -> None:
    with pytest.raises(ImageGenerationError, match="openai"):
        resolve_model("midjourney:v7")


def test_model_without_provider_prefix_rejected() -> None:
    """이름만 보고 프로바이더를 추측하면 새 모델이 나올 때마다 규칙이 깨진다."""
    with pytest.raises(ImageGenerationError, match="provider:model"):
        resolve_model("gpt-image-1")


def test_empty_model_name_rejected() -> None:
    with pytest.raises(ImageGenerationError, match="provider:model"):
        resolve_model("openai:")


def test_every_provider_declares_its_key_env() -> None:
    """키 env가 비면 '키가 없습니다' 안내가 빈 이름으로 나간다."""
    for name, provider in PROVIDERS.items():
        assert provider.env_key, f"{name}의 env_key 없음"
        assert provider.signup_hint, f"{name}의 발급 안내 없음"


# ── Google ────────────────────────────────────────────────────────


def test_google_sends_key_model_and_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_GEMINI_API_KEY, "secret-key")
    seen: list[Any] = []
    result = generate_image(
        "blue cube", model=GOOGLE_MODEL, opener=opener_for(google_body([inline()]), seen=seen)
    )
    assert result == PNG
    [request] = seen
    assert request.headers["X-goog-api-key"] == "secret-key"
    assert "gemini-3.1-flash-lite-image:generateContent" in request.full_url
    assert "blue cube" in request.data.decode()


def test_google_skips_text_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    """모델이 설명 문장을 같이 얹어 보내는 경우가 있다."""
    monkeypatch.setenv(ENV_GEMINI_API_KEY, "k")
    payload = google_body([{"text": "여기 있습니다"}, inline()])
    assert generate_image("cube", model=GOOGLE_MODEL, opener=opener_for(payload)) == PNG


def test_google_text_only_response_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_GEMINI_API_KEY, "k")
    payload = google_body([{"text": "만들 수 없습니다"}])
    with pytest.raises(ImageGenerationError, match="이미지"):
        generate_image("cube", model=GOOGLE_MODEL, opener=opener_for(payload))


def test_google_rejects_non_image_mime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_GEMINI_API_KEY, "k")
    payload = google_body([inline(mime="application/pdf")])
    with pytest.raises(ImageGenerationError, match="mime"):
        generate_image("cube", model=GOOGLE_MODEL, opener=opener_for(payload))


def test_google_quota_error_explains_billing(monkeypatch: pytest.MonkeyPatch) -> None:
    """무료 티어는 이미지 할당량이 0이다 — 429가 기본 상태라 사유가 읽혀야 한다."""
    monkeypatch.setenv(ENV_GEMINI_API_KEY, "k")
    with pytest.raises(ImageGenerationError, match="결제"):
        generate_image("cube", model=GOOGLE_MODEL, opener=_http_error(429))


# ── OpenAI ────────────────────────────────────────────────────────


def test_openai_sends_bearer_model_and_square_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPENAI_API_KEY, "sk-secret")
    seen: list[Any] = []
    result = generate_image(
        "blue cube", model=OPENAI_MODEL, opener=opener_for(openai_body(), seen=seen)
    )
    assert result == PNG
    [request] = seen
    assert request.headers["Authorization"] == "Bearer sk-secret"
    assert request.full_url == "https://api.openai.com/v1/images/generations"
    body = json.loads(request.data)
    assert body["model"] == "gpt-image-1"
    assert body["size"] == "1024x1024"  # 정사각 슬롯이라 크롭 손실이 없다
    assert "blue cube" in body["prompt"]


def test_openai_does_not_send_response_format_for_gpt_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gpt-image-1은 response_format을 받지 않는다 — 보내면 400이다."""
    monkeypatch.setenv(ENV_OPENAI_API_KEY, "sk")
    seen: list[Any] = []
    generate_image("cube", model=OPENAI_MODEL, opener=opener_for(openai_body(), seen=seen))
    assert "response_format" not in json.loads(seen[0].data)


def test_openai_asks_dalle_for_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    """dall-e는 기본이 URL이라 명시하지 않으면 바이트를 못 받는다."""
    monkeypatch.setenv(ENV_OPENAI_API_KEY, "sk")
    seen: list[Any] = []
    generate_image("cube", model="openai:dall-e-3", opener=opener_for(openai_body(), seen=seen))
    assert json.loads(seen[0].data)["response_format"] == "b64_json"


def test_openai_url_only_response_explains_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """URL만 오면 2차 요청이 필요한데, 임의 호스트를 때리는 통로를 열지 않는다."""
    monkeypatch.setenv(ENV_OPENAI_API_KEY, "sk")
    payload = openai_body({"url": "https://oaidalleapi.blob.core.windows.net/x.png"})
    with pytest.raises(ImageGenerationError, match="b64_json"):
        generate_image("cube", model=OPENAI_MODEL, opener=opener_for(payload))


def test_openai_empty_data_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPENAI_API_KEY, "sk")
    with pytest.raises(ImageGenerationError, match="응답"):
        generate_image("cube", model=OPENAI_MODEL, opener=opener_for(b'{"data": []}'))


def test_openai_quota_error_mentions_its_own_billing(monkeypatch: pytest.MonkeyPatch) -> None:
    """할당량 안내가 Google 문구로 나가면 엉뚱한 콘솔을 열게 된다."""
    monkeypatch.setenv(ENV_OPENAI_API_KEY, "sk")
    with pytest.raises(ImageGenerationError, match="platform.openai.com"):
        generate_image("cube", model=OPENAI_MODEL, opener=_http_error(429))


# ── 공통 실패 경로 ────────────────────────────────────────────────


def _http_error(code: int) -> Any:
    import urllib.error

    @contextmanager
    def boom(target: Any, timeout: float = 0) -> Any:
        raise urllib.error.HTTPError(target, code, "nope", None, None)  # type: ignore[arg-type]
        yield

    return boom


@pytest.mark.parametrize("model", [GOOGLE_MODEL, OPENAI_MODEL])
def test_missing_key_names_the_env_and_where_to_get_it(
    model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, _ = resolve_model(model)
    monkeypatch.delenv(provider.env_key, raising=False)
    with pytest.raises(ImageGenerationError, match=provider.env_key):
        generate_image("cube", model=model, opener=opener_for(b"{}"))


@pytest.mark.parametrize("model", [GOOGLE_MODEL, OPENAI_MODEL])
def test_blocked_subject_never_reaches_the_network(
    model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-Q7 게이트는 **요청 전에** 돈다 — 금지 소재로 유료 호출을 내보내지 않는다."""
    provider, _ = resolve_model(model)
    monkeypatch.setenv(provider.env_key, "k")
    seen: list[Any] = []
    with pytest.raises(ImageGenerationError, match="금지 소재"):
        generate_image("a nude portrait", model=model, opener=opener_for(b"{}", seen=seen))
    assert seen == [], "차단된 주제로 요청이 나감"


@pytest.mark.parametrize("model", [GOOGLE_MODEL, OPENAI_MODEL])
def test_oversized_image_rejected(model: str, monkeypatch: pytest.MonkeyPatch) -> None:
    provider, _ = resolve_model(model)
    monkeypatch.setenv(provider.env_key, "k")
    huge = base64.b64encode(b"x" * (MAX_IMAGE_BYTES + 1)).decode()
    payload = (
        google_body([{"inlineData": {"mimeType": "image/png", "data": huge}}])
        if provider.name == "google"
        else openai_body({"b64_json": huge})
    )
    with pytest.raises(ImageGenerationError, match="크기"):
        generate_image("cube", model=model, opener=opener_for(payload))


@pytest.mark.parametrize("model", [GOOGLE_MODEL, OPENAI_MODEL])
def test_malformed_json_raises(model: str, monkeypatch: pytest.MonkeyPatch) -> None:
    provider, _ = resolve_model(model)
    monkeypatch.setenv(provider.env_key, "k")
    with pytest.raises(ImageGenerationError, match="응답"):
        generate_image("cube", model=model, opener=opener_for(b"not json"))
