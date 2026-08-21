"""저장소 seam (FR-M3) — 콘텐츠 주소화 + 되읽기.

`get`이 생긴 이유: 주제 사진은 생성 시점에 저장되고 **렌더 시점에 다시 읽힌다**.
쓰기만 있는 저장소로는 그 왕복이 성립하지 않는다.
"""

import pytest

from sns.render.storage import InMemoryMediaStore


def test_put_returns_content_addressed_url() -> None:
    store = InMemoryMediaStore()
    url = store.put(b"data", checksum="abc123", kind="image", ext="png")
    assert url == "mem://image/abc123.png"


def test_same_checksum_same_url() -> None:
    store = InMemoryMediaStore()
    a = store.put(b"data", checksum="abc123", kind="image", ext="png")
    b = store.put(b"data", checksum="abc123", kind="image", ext="png")
    assert a == b


def test_get_round_trips_bytes() -> None:
    store = InMemoryMediaStore()
    url = store.put(b"\x89PNG bytes", checksum="deadbeef", kind="image", ext="png")
    assert store.get(url) == b"\x89PNG bytes"


def test_get_unknown_url_raises() -> None:
    with pytest.raises(KeyError):
        InMemoryMediaStore().get("mem://image/missing.png")


# ── 최근 발행 주제 조회 (중복 차단의 재료) ────────────────────────


def test_recent_topics_returns_saved_titles() -> None:
    from sns.runner.store import InMemoryCycleStore

    store = InMemoryCycleStore()
    store.save_topic(title="cursor/plugins", summary="요약", source="github_trending")
    store.save_topic(title="vercel/next.js", summary="요약", source="github_trending")
    assert set(store.recent_topic_titles(days=14)) == {"cursor/plugins", "vercel/next.js"}


def test_recent_topics_empty_on_fresh_store() -> None:
    from sns.runner.store import InMemoryCycleStore

    assert InMemoryCycleStore().recent_topic_titles(days=14) == ()


# ── 원장 되읽기 (발행 스크립트가 산출물을 확인하는 경로) ──────────


def _seeded() -> tuple[object, str, str, str]:
    from sns.runner.store import InMemoryCycleStore

    store = InMemoryCycleStore()
    cycle = store.create_cycle("engagement_depth")
    topic = store.save_topic(title="주제", summary="요약", source="github_trending")
    item = store.save_content_item(
        cycle_id=cycle, topic_id=topic, content_format="shorts", body="캡션",
        media_spec={"topic": "t", "slides": []}, hook_pattern="curiosity", status="approved",
    )  # fmt: skip
    asset = store.save_media_asset(
        content_item_id=item, kind="video", storage_url="C:/x.mp4", checksum="abc",
        quality_status="passed", quality_report=None,
    )  # fmt: skip
    return store, topic, item, asset


def test_read_content_item_round_trips() -> None:
    store, _, item, _ = _seeded()
    row = store.read_content_item(item)  # type: ignore[attr-defined]
    assert row["body"] == "캡션"
    assert row["hook_pattern"] == "curiosity"
    assert row["media_spec"] == {"topic": "t", "slides": []}


def test_read_media_asset_round_trips() -> None:
    store, _, _, asset = _seeded()
    row = store.read_media_asset(asset)  # type: ignore[attr-defined]
    assert row["storage_url"] == "C:/x.mp4"
    assert row["checksum"] == "abc"
    assert row["quality_status"] == "passed"


def test_read_topic_round_trips() -> None:
    store, topic, _, _ = _seeded()
    assert store.read_topic(topic)["title"] == "주제"  # type: ignore[attr-defined]
