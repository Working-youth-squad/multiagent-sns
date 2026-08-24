"""인스타그램 어댑터 — 컨테이너 2단계, 오류 분류, 가짜 Graph HTTP, 상태머신 결합. 네트워크 0."""

from typing import Any

import pytest

from sns.adapters.instagram.publisher import (
    GraphError,
    InstagramPublish,
    classify_graph_error,
)
from sns.publish.state_machine import run_publish
from sns.publish.stores import InMemoryPublishAttemptStore
from sns.tools.contracts import MediaAsset

IMAGE = MediaAsset(kind="image", storage_url="mem://image/abc.png", checksum="abc")
VIDEO = MediaAsset(kind="video", storage_url="mem://video/abc.mp4", checksum="abc")


# ── classify_graph_error 표 전체 ─────────────────────────────────────


@pytest.mark.parametrize(
    ("http_status", "code", "subcode", "expected"),
    [
        (400, 190, None, "auth"),
        (400, 4, None, "quota"),
        (400, 613, None, "quota"),
        (400, 10, 2207003, "spam_block"),
        (0, None, None, "transient"),
        (500, None, None, "transient"),
        (400, 100, None, "permanent_unknown"),
    ],
)
def test_classify_graph_error(
    http_status: int, code: int | None, subcode: int | None, expected: str
) -> None:
    assert classify_graph_error(http_status=http_status, code=code, subcode=subcode) == expected


# ── 가짜 GraphHttp ────────────────────────────────────────────────────


class _FakeGraphHttp:
    """호출 순서대로 미리 등록한 응답/예외를 돌려준다."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, str]]] = []
        self.gets: list[tuple[str, dict[str, str]]] = []
        self._post_queue: list[dict[str, Any] | Exception] = []
        self._get_queue: list[dict[str, Any] | Exception] = []

    def queue_post(self, response: dict[str, Any] | Exception) -> None:
        self._post_queue.append(response)

    def queue_get(self, response: dict[str, Any] | Exception) -> None:
        self._get_queue.append(response)

    def post(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        self.posts.append((path, params))
        result = self._post_queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        self.gets.append((path, params))
        result = self._get_queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _publish(http: _FakeGraphHttp) -> InstagramPublish:
    return InstagramPublish(
        http,
        ig_user_id="ig-user-1",
        access_token="tok",
        media_url_resolver=lambda u: f"https://cdn/{u}",
    )


# ── 컨테이너 생성 단계 ────────────────────────────────────────────────


def test_create_container_returns_transient_with_container_id() -> None:
    http = _FakeGraphHttp()
    http.queue_post({"id": "container-1"})
    result = _publish(http)("instagram", IMAGE, "caption", "idem-1")
    assert result.container_id == "container-1"
    assert result.error is not None and result.error.error_class == "transient"
    assert result.post_id is None
    path, params = http.posts[0]
    assert path == "/ig-user-1/media"
    assert params["image_url"] == "https://cdn/mem://image/abc.png"
    assert "media_type" not in params


def test_create_container_video_uses_reels_media_type() -> None:
    http = _FakeGraphHttp()
    http.queue_post({"id": "container-2"})
    _publish(http)("instagram", VIDEO, "caption", "idem-1")
    _, params = http.posts[0]
    assert params["media_type"] == "REELS"
    assert params["video_url"] == "https://cdn/mem://video/abc.mp4"


# ── 폴링·게시 단계 ────────────────────────────────────────────────────


def test_advance_in_progress_stays_transient() -> None:
    http = _FakeGraphHttp()
    http.queue_get({"status_code": "IN_PROGRESS"})
    result = _publish(http)("instagram", IMAGE, "caption", "idem-1", container_id="container-1")
    assert result.container_id == "container-1"
    assert result.error is not None and result.error.error_class == "transient"


def test_advance_finished_publishes_and_returns_post_id() -> None:
    http = _FakeGraphHttp()
    http.queue_get({"status_code": "FINISHED"})
    http.queue_post({"id": "post-99"})
    result = _publish(http)("instagram", IMAGE, "caption", "idem-1", container_id="container-1")
    assert result.post_id == "post-99"
    assert result.error is None
    path, params = http.posts[0]
    assert path == "/ig-user-1/media_publish"
    assert params["creation_id"] == "container-1"


def test_advance_error_status_is_permanent() -> None:
    http = _FakeGraphHttp()
    http.queue_get({"status_code": "ERROR"})
    result = _publish(http)("instagram", IMAGE, "caption", "idem-1", container_id="container-1")
    assert result.error is not None and result.error.error_class == "permanent_unknown"


def test_graph_error_classified_via_exception() -> None:
    http = _FakeGraphHttp()
    http.queue_post(GraphError(http_status=400, code=190, subcode=None, raw="token expired"))
    result = _publish(http)("instagram", IMAGE, "caption", "idem-1")
    assert result.error is not None
    assert result.error.error_class == "auth"
    assert result.error.error_raw == "token expired"


def test_wrong_platform_or_kind_is_programming_error() -> None:
    publish = _publish(_FakeGraphHttp())
    with pytest.raises(ValueError):
        publish("youtube", IMAGE, "c", "k")
    with pytest.raises(ValueError):
        publish("instagram", MediaAsset(kind="thumbnail", storage_url="u", checksum="c"), "c", "k")


# ── 상태머신 결합 (FR-P3·P4) ─────────────────────────────────────────


def _run(store: InMemoryPublishAttemptStore, http: _FakeGraphHttp) -> Any:
    return run_publish(
        store=store,
        publish=_publish(http),
        publication_id="pub-1",
        platform="instagram",
        media=IMAGE,
        caption="caption",
        idempotency_key="idem-1",
        quality_passed=True,
    )


def test_state_machine_two_stage_reaches_published() -> None:
    store = InMemoryPublishAttemptStore()
    http = _FakeGraphHttp()
    http.queue_post({"id": "container-1"})
    attempt = _run(store, http)
    assert attempt.state == "container_created" and attempt.container_id == "container-1"

    http2 = _FakeGraphHttp()
    http2.queue_get({"status_code": "FINISHED"})
    http2.queue_post({"id": "post-1"})
    published = run_publish(
        store=store,
        publish=_publish(http2),
        publication_id="pub-1",
        platform="instagram",
        media=IMAGE,
        caption="caption",
        idempotency_key="idem-1",
        quality_passed=True,
    )
    assert published.state == "published" and published.external_post_id == "post-1"

    # 재구동 — 종결 상태라 툴 재호출 없이 저장분 반환.
    again = _run(store, _FakeGraphHttp())
    assert again == published


def test_state_machine_permanent_error_goes_failed_and_stops_polling() -> None:
    store = InMemoryPublishAttemptStore()
    http = _FakeGraphHttp()
    http.queue_post({"id": "container-1"})
    _run(store, http)

    http2 = _FakeGraphHttp()
    http2.queue_get({"status_code": "ERROR"})
    failed = run_publish(
        store=store,
        publish=_publish(http2),
        publication_id="pub-1",
        platform="instagram",
        media=IMAGE,
        caption="caption",
        idempotency_key="idem-1",
        quality_passed=True,
    )
    assert failed.state == "failed" and failed.error_class == "permanent_unknown"

    http3 = _FakeGraphHttp()
    again = _run(store, http3)
    assert again.state == "failed"
    assert http3.posts == [] and http3.gets == []  # failed는 종결 — 재호출 0
