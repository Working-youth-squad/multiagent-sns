"""유튜브 어댑터 — caption 패킹, 오류 분류, 가짜 업로드, 상태머신 결합. 네트워크 0."""

from typing import Any

import pytest

from sns.adapters.youtube.publisher import YouTubePublish, classify_http_error, split_caption
from sns.publish.state_machine import run_publish
from sns.publish.stores import InMemoryPublishAttemptStore
from sns.tools.contracts import MediaAsset

MEDIA = MediaAsset(kind="video", storage_url="mem://video/abc.mp4", checksum="abc")


# ── split_caption ────────────────────────────────────────────────────


def test_split_caption_appends_shorts_tag() -> None:
    title, desc = split_caption("오늘의 한 줄\n본문 설명\n둘째 줄")
    assert title == "오늘의 한 줄 #Shorts"
    assert desc == "본문 설명\n둘째 줄"


def test_split_caption_keeps_existing_tag_and_limits_length() -> None:
    title, _ = split_caption("이미 #shorts 포함된 제목")
    assert "#Shorts" not in title  # 기존 태그 존중, 중복 부착 없음
    long_title, _ = split_caption("가" * 200)
    assert len(long_title) <= 100
    assert long_title.endswith("#Shorts")


def test_split_caption_empty_title_fallback() -> None:
    title, _ = split_caption("\n설명만 있음")
    assert title == "Untitled #Shorts"


# ── classify_http_error 표 전체 ──────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        (401, "", "auth"),
        (403, "quotaExceeded", "quota"),
        (403, "rateLimitExceeded", "quota"),
        (403, "uploadLimitExceeded", "quota"),
        (403, "forbidden", "auth"),
        (500, "", "transient"),
        (503, "backendError", "transient"),
        (400, "invalidRequest", "permanent_unknown"),
    ],
)
def test_classify_http_error(status: int, reason: str, expected: str) -> None:
    assert classify_http_error(status, reason) == expected


# ── 가짜 youtube 클라이언트 ──────────────────────────────────────────


class _FakeRequest:
    def __init__(self, response: dict[str, Any] | None, exc: Exception | None = None) -> None:
        self._response = response
        self._exc = exc

    def next_chunk(self) -> tuple[None, dict[str, Any] | None]:
        if self._exc is not None:
            raise self._exc
        return None, self._response


class _FakeYouTube:
    def __init__(self, response: dict[str, Any] | None = None, exc: Exception | None = None):
        self._request = _FakeRequest(response, exc)
        self.inserted_bodies: list[dict[str, Any]] = []

    def videos(self) -> "_FakeYouTube":
        return self

    def insert(self, *, part: str, body: dict[str, Any], media_body: Any) -> _FakeRequest:
        self.inserted_bodies.append(body)
        return self._request


def _publish(yt: _FakeYouTube) -> YouTubePublish:
    return YouTubePublish(yt, media_bytes=lambda _url: b"fake-mp4")


def test_upload_success_returns_video_id() -> None:
    yt = _FakeYouTube(response={"id": "vid123"})
    result = _publish(yt)("youtube", MEDIA, "제목\n설명", "idem-1")
    assert result.post_id == "vid123"
    assert result.error is None
    body = yt.inserted_bodies[0]
    assert body["snippet"]["title"] == "제목 #Shorts"
    assert body["status"]["privacyStatus"] == "private"


def test_upload_transient_error_classified() -> None:
    yt = _FakeYouTube(exc=TimeoutError("socket timeout"))
    result = _publish(yt)("youtube", MEDIA, "제목", "idem-1")
    assert result.error is not None and result.error.error_class == "transient"


def test_wrong_platform_or_kind_is_programming_error() -> None:
    publish = _publish(_FakeYouTube(response={"id": "x"}))
    with pytest.raises(ValueError):
        publish("instagram", MEDIA, "c", "k")
    with pytest.raises(ValueError):
        publish("youtube", MediaAsset(kind="image", storage_url="u", checksum="c"), "c", "k")


# ── 상태머신 결합 (FR-P3·P4) ─────────────────────────────────────────


def _run(store: InMemoryPublishAttemptStore, yt: _FakeYouTube) -> Any:
    return run_publish(
        store=store,
        publish=_publish(yt),
        publication_id="pub-1",
        platform="youtube",
        media=MEDIA,
        caption="제목\n설명",
        idempotency_key="idem-1",
        quality_passed=True,
    )


def test_state_machine_published_never_recalls_tool() -> None:
    store = InMemoryPublishAttemptStore()
    yt = _FakeYouTube(response={"id": "vid123"})
    attempt = _run(store, yt)
    assert attempt.state == "published" and attempt.external_post_id == "vid123"
    again = _run(store, yt)  # 재구동 — 툴 재호출 없이 저장분 반환
    assert again == attempt
    assert len(yt.inserted_bodies) == 1


def test_state_machine_transient_stays_retryable() -> None:
    store = InMemoryPublishAttemptStore()
    attempt = _run(store, _FakeYouTube(exc=TimeoutError("flaky")))
    assert attempt.state == "pending" and attempt.error_class == "transient"
    recovered = _run(store, _FakeYouTube(response={"id": "vid456"}))  # 재시도 → 성공
    assert recovered.state == "published" and recovered.external_post_id == "vid456"


def test_state_machine_permanent_error_goes_failed() -> None:
    store = InMemoryPublishAttemptStore()
    attempt = _run(store, _FakeYouTube(exc=RuntimeError("boom")))
    assert attempt.state == "failed" and attempt.error_class == "permanent_unknown"
    yt = _FakeYouTube(response={"id": "never"})
    again = _run(store, yt)  # failed는 종결 — 재호출 0
    assert again.state == "failed"
    assert yt.inserted_bodies == []
