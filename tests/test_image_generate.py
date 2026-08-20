"""생성 이미지 어댑터 — 네트워크 0(opener 주입).

무료 티어 할당량이 0이라 **결제를 켜야만** 실제로 돈다. 그래서 이 테스트가 검증하는 건
"실제 그림이 예쁜가"가 아니라 그 앞의 계약이다: 프롬프트를 어떻게 짜서 보내는가,
FR-Q7 게이트가 요청 전에 도는가, 응답을 어떻게 해석하고 실패를 어떻게 돌려주는가.
"""

import base64
import io
import json
from contextlib import contextmanager
from typing import Any

import pytest

from sns.render.images.generate import (
    ENV_GEMINI_API_KEY,
    IMAGE_MODEL,
    MAX_IMAGE_BYTES,
    STYLE_RULES,
    ImageGenerationError,
    build_prompt,
    generate_image,
    parse_image_response,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"fake bytes"


def body(parts: list[dict[str, Any]]) -> bytes:
    return json.dumps({"candidates": [{"content": {"parts": parts}}]}).encode()


def inline(data: bytes = PNG, mime: str = "image/png") -> dict[str, Any]:
    return {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}


def opener_for(payload: bytes, *, seen: list[Any] | None = None) -> Any:
    @contextmanager
    def _open(target: Any, timeout: float = 0) -> Any:
        if seen is not None:
            seen.append(target)
        yield io.BytesIO(payload)

    return _open


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


# ── 응답 파싱 ─────────────────────────────────────────────────────


def test_parse_returns_image_bytes() -> None:
    assert parse_image_response(body([inline()])) == PNG


def test_parse_skips_text_parts() -> None:
    """모델이 설명 문장을 같이 얹어 보내는 경우가 있다."""
    assert parse_image_response(body([{"text": "여기 있습니다"}, inline()])) == PNG


def test_parse_without_image_raises() -> None:
    with pytest.raises(ImageGenerationError, match="이미지"):
        parse_image_response(body([{"text": "만들 수 없습니다"}]))


def test_parse_malformed_json_raises() -> None:
    with pytest.raises(ImageGenerationError, match="응답"):
        parse_image_response(b"not json")


def test_parse_rejects_non_image_mime() -> None:
    with pytest.raises(ImageGenerationError, match="mime"):
        parse_image_response(body([inline(mime="application/pdf")]))


def test_parse_rejects_oversized_image() -> None:
    with pytest.raises(ImageGenerationError, match="크기"):
        parse_image_response(body([inline(b"x" * (MAX_IMAGE_BYTES + 1))]))


def test_parse_rejects_bad_base64() -> None:
    payload = json.dumps(
        {
            "candidates": [
                {"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": "!!"}}]}}
            ]
        }
    ).encode()
    with pytest.raises(ImageGenerationError, match="디코드"):
        parse_image_response(payload)


# ── 호출 ──────────────────────────────────────────────────────────


def test_generate_sends_key_model_and_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_GEMINI_API_KEY, "secret-key")
    seen: list[Any] = []
    assert generate_image("blue cube", opener=opener_for(body([inline()]), seen=seen)) == PNG
    [request] = seen
    assert request.headers["X-goog-api-key"] == "secret-key"
    assert IMAGE_MODEL in request.full_url
    assert "blue cube" in request.data.decode()


def test_generate_without_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_GEMINI_API_KEY, raising=False)
    with pytest.raises(ImageGenerationError, match=ENV_GEMINI_API_KEY):
        generate_image("blue cube", opener=opener_for(body([inline()])))


def test_generate_rejects_blocked_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-Q7 게이트는 **요청 전에** 돈다 — 금지 소재로 유료 호출을 내보내지 않는다."""
    monkeypatch.setenv(ENV_GEMINI_API_KEY, "secret-key")
    seen: list[Any] = []
    with pytest.raises(ImageGenerationError, match="금지 소재"):
        generate_image("a nude portrait", opener=opener_for(body([inline()]), seen=seen))
    assert seen == [], "차단된 주제로 요청이 나감"


def test_generate_maps_quota_error_to_a_readable_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """무료 티어는 이미지 할당량이 0이다 — 429가 기본 상태라 사유가 읽혀야 한다."""
    import urllib.error

    monkeypatch.setenv(ENV_GEMINI_API_KEY, "secret-key")

    @contextmanager
    def boom(target: Any, timeout: float = 0) -> Any:
        raise urllib.error.HTTPError(target, 429, "Too Many Requests", None, None)  # type: ignore[arg-type]
        yield

    with pytest.raises(ImageGenerationError, match="결제"):
        generate_image("blue cube", opener=boom)
