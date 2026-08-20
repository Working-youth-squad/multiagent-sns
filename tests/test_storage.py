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
