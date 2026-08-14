"""Discord 전송 — 순수 페이로드 렌더 + opener 주입(네트워크 없음)."""

import json
import urllib.request

import pytest

from sns.notify.alerts import publish_failure, publish_success, quota_exceeded
from sns.notify.discord import (
    DiscordError,
    discord_payload,
    discord_sender_from_env,
    post_discord,
)
from sns.tools.contracts import ToolError


def test_payload_failure_carries_class_and_context() -> None:
    alert = publish_failure(
        "instagram", ToolError("quota", "rate limit exceeded"), publication_id="pub-1"
    )
    embed = discord_payload(alert)["embeds"][0]  # type: ignore[index]
    assert embed["title"] == "[instagram] 발행 실패 — quota"
    desc = embed["description"]
    assert "분류: quota" in desc
    assert "원문: rate limit exceeded" in desc
    assert "publication_id: pub-1" in desc


def test_payload_includes_cause_line_when_present() -> None:
    base = quota_exceeded("youtube", detail="dailyLimitExceeded")
    from dataclasses import replace

    alert = replace(base, cause_line="일일 API 쿼터를 소진한 것으로 보인다.")
    desc = discord_payload(alert)["embeds"][0]["description"]  # type: ignore[index]
    assert "원인: 일일 API 쿼터를 소진한 것으로 보인다." in desc


def test_payload_success_has_no_error_lines() -> None:
    alert = publish_success("youtube", post_id="vid-9", publication_id="pub-2")
    embed = discord_payload(alert)["embeds"][0]  # type: ignore[index]
    assert "분류" not in embed.get("description", "")
    assert (
        embed["color"]
        != discord_payload(  # 성공색 ≠ 실패색
            publish_failure("youtube", ToolError("auth", "x"), publication_id="p")
        )["embeds"][0]["color"]
    )  # type: ignore[index]


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self, amt: int = -1) -> bytes:
        return b""


def test_post_sends_json_post_with_headers_and_timeout() -> None:
    sink: dict[str, object] = {}

    def opener(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        sink["url"] = request.full_url
        sink["method"] = request.method
        sink["ctype"] = request.headers.get("Content-type")
        sink["body"] = request.data
        sink["timeout"] = timeout
        return _FakeResponse(204)

    payload = {"embeds": [{"title": "안녕"}]}
    post_discord(payload, webhook_url="https://discord/hook", timeout_s=3.0, opener=opener)

    assert sink["url"] == "https://discord/hook"
    assert sink["method"] == "POST"
    assert sink["ctype"] == "application/json"
    assert sink["timeout"] == 3.0
    assert json.loads(sink["body"]) == payload  # type: ignore[arg-type]


def test_post_raises_on_non_2xx() -> None:
    with pytest.raises(DiscordError):
        post_discord({"x": 1}, webhook_url="https://d/h", opener=lambda *a, **k: _FakeResponse(500))


def test_post_wraps_network_error() -> None:
    def opener(*a: object, **k: object) -> _FakeResponse:
        raise OSError("connection refused")

    with pytest.raises(DiscordError):
        post_discord({"x": 1}, webhook_url="https://d/h", opener=opener)


def test_sender_from_env_none_when_unset() -> None:
    assert discord_sender_from_env({}) is None
    assert discord_sender_from_env({"DISCORD_WEBHOOK_URL": "https://d/h"}) is not None
