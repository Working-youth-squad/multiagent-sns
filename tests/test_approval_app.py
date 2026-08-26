"""승인 화면 FastAPI 앱 — InMemoryApprovalStore로 DB 없이 라우팅·폼 처리 검증."""

from collections.abc import Mapping
from dataclasses import replace

from fastapi.testclient import TestClient

from sns.tools.contracts import MediaAsset
from sns.web.approve.app import create_app
from sns.web.approve.store import InMemoryApprovalStore, PendingItem

ITEM = PendingItem(
    content_item_id="ci-1",
    cycle_id="cy-1",
    topic_title="가을 산책 코스",
    content_format="feed_image",
    hook_pattern="curiosity",
    body="원본 초안",
    media_asset_id="ma-1",
    media_kind="image",
    media_storage_url="mem://x",
    quality_status="needs_review",
    publication_id="pub-1",
    channel_id="ch-1",
    platform="instagram",
    handle="demo",
)


def _client(store: InMemoryApprovalStore) -> TestClient:
    return TestClient(create_app(store))


def test_list_shows_seeded_item() -> None:
    client = _client(InMemoryApprovalStore((ITEM,)))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "가을 산책 코스" in resp.text


def test_list_empty_shows_empty_message() -> None:
    client = _client(InMemoryApprovalStore())
    resp = client.get("/")
    assert "승인 대기 중인 항목이 없습니다" in resp.text


def test_detail_shows_item_form() -> None:
    client = _client(InMemoryApprovalStore((ITEM,)))
    resp = client.get("/items/ci-1")
    assert resp.status_code == 200
    assert "원본 초안" in resp.text


def test_detail_missing_returns_404() -> None:
    client = _client(InMemoryApprovalStore())
    resp = client.get("/items/nope")
    assert resp.status_code == 404


def test_approve_redirects_and_records_body() -> None:
    store = InMemoryApprovalStore((ITEM,))
    client = _client(store)
    resp = client.post("/items/ci-1/approve", data={"body": "수정된 본문"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert store.approved["ci-1"] == "수정된 본문"
    assert store.list_pending() == ()  # 승인 후 대기 목록에서 사라짐


def test_approve_missing_returns_404() -> None:
    client = _client(InMemoryApprovalStore())
    resp = client.post("/items/nope/approve", data={"body": "x"})
    assert resp.status_code == 404


def test_reject_redirects_and_records_reason() -> None:
    store = InMemoryApprovalStore((ITEM,))
    client = _client(store)
    resp = client.post("/items/ci-1/reject", data={"reason": "톤 부적절"}, follow_redirects=False)
    assert resp.status_code == 303
    assert store.rejected["ci-1"] == "톤 부적절"
    assert store.list_pending() == ()


def test_reject_without_reason_defaults() -> None:
    store = InMemoryApprovalStore((ITEM,))
    client = _client(store)
    client.post("/items/ci-1/reject", data={}, follow_redirects=False)
    assert store.rejected["ci-1"] == "사유 미기재"


def test_double_approve_second_call_is_404() -> None:
    store = InMemoryApprovalStore((ITEM,))
    client = _client(store)
    client.post("/items/ci-1/approve", data={"body": "본문"})
    resp = client.post("/items/ci-1/approve", data={"body": "본문"})
    assert resp.status_code == 404


# ── 영상 재렌더 ───────────────────────────────────────────────────

VIDEO_SPEC: dict[str, object] = {
    "topic": "리스트에서 in 쓰지 마세요",
    "slides": [{"subtitle": "왜 느린가", "narration": "in 연산자는 전부 훑습니다."}],
    "character_ref": "file:///characters/c.png",
}
VIDEO_ITEM = replace(
    ITEM, content_item_id="ci-v", content_format="shorts", media_kind="video",
    media_spec=VIDEO_SPEC,
)  # fmt: skip


def fake_rerender(
    spec: Mapping[str, object],
) -> tuple[MediaAsset, str, Mapping[str, object] | None]:
    return MediaAsset(kind="video", storage_url="mem://new.mp4", checksum="chk-2"), "passed", None


def test_rerender_updates_spec_and_redirects_to_detail() -> None:
    store = InMemoryApprovalStore((VIDEO_ITEM,))
    client = TestClient(create_app(store, rerender_video=fake_rerender))
    resp = client.post(
        "/items/ci-v/rerender",
        data={"topic": "고친 주제", "subtitle_0": "고친 부제", "narration_0": "고친 나레이션."},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/items/ci-v?rerendered=1"
    spec = store.rerendered["ci-v"]
    assert spec["topic"] == "고친 주제"
    slide = spec["slides"][0]  # type: ignore[index]
    assert slide["subtitle"] == "고친 부제" and slide["narration"] == "고친 나레이션."
    assert spec["character_ref"] == "file:///characters/c.png"  # 편집 폼에 없는 필드 보존
    item = store.get_pending("ci-v")
    assert item is not None and item.media_storage_url == "mem://new.mp4"
    assert item.quality_status == "passed"


def test_rerender_over_width_shows_error_without_render() -> None:
    """글자수 상한(spec 파서)이 가드레일 — 유료 렌더 전에 끊고 수정값을 유지 표시한다."""
    calls: list[object] = []

    def spy(spec: Mapping[str, object]) -> tuple[MediaAsset, str, None]:
        calls.append(spec)
        raise AssertionError("호출되면 안 된다")

    store = InMemoryApprovalStore((VIDEO_ITEM,))
    client = TestClient(create_app(store, rerender_video=spy))
    resp = client.post(
        "/items/ci-v/rerender",
        data={"topic": "주제", "subtitle_0": "부제", "narration_0": "너무 긴 나레이션 " * 10},
    )
    assert resp.status_code == 422
    assert "narration" in resp.text
    assert "너무 긴 나레이션" in resp.text  # 수정값 유지
    assert calls == []


def test_rerender_render_failure_surfaces_message() -> None:
    def boom(spec: Mapping[str, object]) -> tuple[MediaAsset, str, None]:
        raise RuntimeError("ffmpeg 실패(exit 1)")

    client = TestClient(create_app(InMemoryApprovalStore((VIDEO_ITEM,)), rerender_video=boom))
    resp = client.post(
        "/items/ci-v/rerender", data={"topic": "주제", "subtitle_0": "부제", "narration_0": "짧게."}
    )
    assert resp.status_code == 502
    assert "재렌더 실패" in resp.text and "ffmpeg" in resp.text


def test_rerender_without_wiring_is_404_and_form_hidden() -> None:
    store = InMemoryApprovalStore((VIDEO_ITEM,))
    client = _client(store)  # rerender_video 미주입
    assert "/items/ci-v/rerender" not in client.get("/items/ci-v").text
    resp = client.post("/items/ci-v/rerender", data={"topic": "x"})
    assert resp.status_code == 404


def test_rerender_on_item_without_spec_is_404() -> None:
    client = TestClient(create_app(InMemoryApprovalStore((ITEM,)), rerender_video=fake_rerender))
    resp = client.post("/items/ci-1/rerender", data={"topic": "x"})
    assert resp.status_code == 404


# ── 미디어 미리보기 ───────────────────────────────────────────────


def test_media_route_serves_local_file(tmp_path: object) -> None:
    from pathlib import Path

    mp4 = Path(str(tmp_path)) / "v.mp4"
    mp4.write_bytes(b"fake-mp4-bytes")
    item = replace(VIDEO_ITEM, media_storage_url=str(mp4))
    client = _client(InMemoryApprovalStore((item,)))
    resp = client.get("/items/ci-v/media")
    assert resp.status_code == 200
    assert resp.content == b"fake-mp4-bytes"
    assert resp.headers["content-type"] == "video/mp4"


def test_media_route_serves_file_uri(tmp_path: object) -> None:
    from pathlib import Path

    png = Path(str(tmp_path)) / "i.png"
    png.write_bytes(b"fake-png")
    item = replace(ITEM, media_storage_url=png.resolve().as_uri())
    client = _client(InMemoryApprovalStore((item,)))
    resp = client.get("/items/ci-1/media")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_media_route_non_local_storage_is_404() -> None:
    client = _client(InMemoryApprovalStore((ITEM,)))  # mem://x — 로컬 파일 아님
    assert client.get("/items/ci-1/media").status_code == 404


def test_detail_shows_rerendered_notice() -> None:
    client = _client(InMemoryApprovalStore((VIDEO_ITEM,)))
    assert "재렌더 완료" in client.get("/items/ci-v?rerendered=1").text
    assert "재렌더 완료" not in client.get("/items/ci-v").text
