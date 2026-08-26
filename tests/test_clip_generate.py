"""생성 클립 어댑터 — long-running 3단(시작→폴링→다운로드), 네트워크 0(opener 주입).

유료 전용이라 이 테스트가 검증하는 건 계약이다: 요청 모양(9:16, predictLongRunning),
게이트가 요청 전에 도는가, 폴링이 완료/실패/타임아웃을 구분하는가.
"""

import io
import json
from contextlib import contextmanager
from typing import Any

import pytest

from sns.agents.models import ENV_GEMINI_API_KEY
from sns.render.clips.generate import (
    DEFAULT_MODEL,
    ENV_CLIP_MODEL,
    ClipGenerationError,
    build_prompt,
    generate_clip,
    parse_operation,
    parse_poll,
    resolve_model,
    start_request,
)

MP4 = b"\x00\x00\x00\x18ftypmp42fake-clip-bytes"
OP = json.dumps({"name": "models/veo/operations/op-1"}).encode()


def done_body(*, key: str = "generatedSamples") -> bytes:
    return json.dumps(
        {
            "done": True,
            "response": {
                "generateVideoResponse": {key: [{"video": {"uri": "https://dl/clip.mp4"}}]}
            },
        }
    ).encode()


def opener_seq(payloads: list[bytes], *, seen: list[Any] | None = None) -> Any:
    """호출 순서대로 payload를 돌려주는 가짜 opener — 시작→폴링→다운로드를 관통한다."""

    @contextmanager
    def _open(target: Any, timeout: float = 0) -> Any:
        if seen is not None:
            seen.append(target)
        yield io.BytesIO(payloads.pop(0))

    return _open


def test_build_prompt_appends_style_rules() -> None:
    prompt = build_prompt("bread steaming")
    assert prompt.startswith("bread steaming.")
    assert "9:16" in prompt and "no text" in prompt


def test_resolve_model_env_then_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_CLIP_MODEL, raising=False)
    assert resolve_model(None) == DEFAULT_MODEL
    monkeypatch.setenv(ENV_CLIP_MODEL, "veo-3.1-fast-generate-preview")
    assert resolve_model(None) == "veo-3.1-fast-generate-preview"
    assert resolve_model("veo-x") == "veo-x"  # 인자 > env


def test_start_request_shape() -> None:
    request = start_request("veo-test", "a prompt", "secret")
    assert "models/veo-test:predictLongRunning" in request.full_url
    assert request.headers["X-goog-api-key"] == "secret"
    body = json.loads(request.data.decode())
    assert body["instances"] == [{"prompt": "a prompt"}]
    assert body["parameters"]["aspectRatio"] == "9:16"  # 쇼츠는 세로다


def test_parse_operation_and_poll_states() -> None:
    assert parse_operation(OP) == "models/veo/operations/op-1"
    with pytest.raises(ClipGenerationError, match="operation"):
        parse_operation(b"{}")
    assert parse_poll(json.dumps({"done": False}).encode()) is None  # 진행 중
    assert parse_poll(done_body()) == "https://dl/clip.mp4"
    assert parse_poll(done_body(key="generatedVideos")) == "https://dl/clip.mp4"  # SDK 세대 차
    with pytest.raises(ClipGenerationError, match="작업 실패"):
        parse_poll(json.dumps({"done": True, "error": {"message": "safety"}}).encode())
    with pytest.raises(ClipGenerationError, match="예상과 다름"):
        parse_poll(json.dumps({"done": True, "response": {}}).encode())


def test_generate_clip_full_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """시작 → 폴링(진행 중 1회) → 폴링(완료) → 다운로드. sleep은 주입으로 0초."""
    monkeypatch.setenv(ENV_GEMINI_API_KEY, "k")
    seen: list[Any] = []
    slept: list[float] = []
    pending = json.dumps({"done": False}).encode()
    result = generate_clip(
        "bread steaming",
        opener=opener_seq([OP, pending, done_body(), MP4], seen=seen),
        sleep=slept.append,
    )
    assert result == MP4
    assert len(seen) == 4 and slept == [5.0]
    assert "predictLongRunning" in seen[0].full_url
    assert seen[1].full_url == seen[2].full_url  # 같은 operation 폴링
    assert seen[3].full_url == "https://dl/clip.mp4"


def test_generate_clip_gate_blocks_before_any_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_GEMINI_API_KEY, "k")
    seen: list[Any] = []
    with pytest.raises(ClipGenerationError, match="게이트"):
        generate_clip("한글 주제", opener=opener_seq([], seen=seen))
    assert seen == []  # 유료 호출 0회


def test_generate_clip_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_GEMINI_API_KEY, raising=False)
    with pytest.raises(ClipGenerationError, match=ENV_GEMINI_API_KEY):
        generate_clip("bread", opener=opener_seq([]))


def test_generate_clip_poll_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_GEMINI_API_KEY, "k")
    pending = json.dumps({"done": False}).encode()
    payloads = [OP] + [pending] * 200  # 완료가 영영 안 오는 작업
    with pytest.raises(ClipGenerationError, match="타임아웃"):
        generate_clip("bread", opener=opener_seq(payloads), sleep=lambda s: None)
