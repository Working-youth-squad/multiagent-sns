"""승인 화면 FastAPI 앱 — InMemoryApprovalStore로 DB 없이 라우팅·폼 처리 검증."""

from fastapi.testclient import TestClient

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
