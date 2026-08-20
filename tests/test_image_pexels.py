"""Pexels 검색·다운로드 어댑터 — 네트워크 0(opener 주입).

`sns.research.sources`가 세운 규율과 같다: 순수 파서 + 얇은 fetch + 주입 opener.
검증 대상은 "Pexels 응답 모양 → StockImage 정규화"와 실패 경로다.
"""

import io
import json
from contextlib import contextmanager
from typing import Any

import pytest

from sns.render.images.gate import StockImage
from sns.render.images.pexels import (
    ENV_PEXELS_API_KEY,
    MAX_IMAGE_BYTES,
    PexelsError,
    download_image,
    parse_search_response,
    search_pexels,
)

PHOTO: dict[str, Any] = {
    "id": 1181671,
    "width": 1920,
    "height": 1280,
    "url": "https://www.pexels.com/photo/1181671/",
    "photographer": "Christina Morillo",
    "alt": "close up of source code on a monitor",
    "src": {
        "original": "https://images.pexels.com/photos/1181671/o.jpeg",
        "large2x": "https://images.pexels.com/photos/1181671/l2x.jpeg",
        "large": "https://images.pexels.com/photos/1181671/l.jpeg",
        "medium": "https://images.pexels.com/photos/1181671/m.jpeg",
    },
}


def body(photos: list[dict[str, Any]]) -> bytes:
    return json.dumps({"total_results": len(photos), "photos": photos}).encode()


def opener_for(payload: bytes, *, seen: list[Any] | None = None) -> Any:
    @contextmanager
    def _open(target: Any, timeout: float = 0) -> Any:
        if seen is not None:
            seen.append(target)
        yield io.BytesIO(payload)

    return _open


# ── 파싱 ──────────────────────────────────────────────────────────


def test_parse_normalizes_to_stock_image() -> None:
    [img] = parse_search_response(body([PHOTO]))
    assert isinstance(img, StockImage)
    assert (img.source, img.source_id, img.license_id) == ("pexels", "1181671", "pexels")
    assert (img.width, img.height) == (1920, 1280)
    assert img.page_url == PHOTO["url"]
    assert img.photographer == "Christina Morillo"


def test_parse_prefers_large2x_over_original() -> None:
    """original은 10MB를 넘기도 한다 — 940 정사각에 쓸 거라 large2x면 충분하다."""
    [img] = parse_search_response(body([PHOTO]))
    assert img.download_url.endswith("l2x.jpeg")


def test_parse_falls_back_when_large2x_missing() -> None:
    photo = {**PHOTO, "src": {"large": "https://images.pexels.com/photos/1/l.jpeg"}}
    [img] = parse_search_response(body([photo]))
    assert img.download_url.endswith("l.jpeg")


def test_parse_skips_photo_without_any_usable_src() -> None:
    assert parse_search_response(body([{**PHOTO, "src": {}}])) == []


def test_parse_empty_results() -> None:
    assert parse_search_response(body([])) == []


def test_parse_missing_alt_becomes_empty_string() -> None:
    photo = dict(PHOTO)
    del photo["alt"]
    [img] = parse_search_response(body([photo]))
    assert img.alt == ""


def test_parse_malformed_json_raises() -> None:
    with pytest.raises(PexelsError, match="응답"):
        parse_search_response(b"not json")


def test_parse_unexpected_shape_raises() -> None:
    with pytest.raises(PexelsError, match="응답"):
        parse_search_response(json.dumps({"photos": "nope"}).encode())


# ── 검색 ──────────────────────────────────────────────────────────


def test_search_sends_api_key_and_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PEXELS_API_KEY, "secret-key")
    seen: list[Any] = []
    result = search_pexels("network cables", limit=5, opener=opener_for(body([PHOTO]), seen=seen))
    [request] = seen
    assert request.headers["Authorization"] == "secret-key"
    assert "query=network+cables" in request.full_url
    assert "per_page=5" in request.full_url
    assert len(result) == 1


def test_search_without_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_PEXELS_API_KEY, raising=False)
    with pytest.raises(PexelsError, match=ENV_PEXELS_API_KEY):
        search_pexels("cables", opener=opener_for(body([PHOTO])))


def test_search_rejects_blocked_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """게이트는 검색 **전에** 돈다 — 금지 소재로 외부 요청 자체를 보내지 않는다."""
    monkeypatch.setenv(ENV_PEXELS_API_KEY, "secret-key")
    seen: list[Any] = []
    with pytest.raises(PexelsError, match="금지 소재"):
        search_pexels("nude portrait", opener=opener_for(body([PHOTO]), seen=seen))
    assert seen == [], "차단된 질의로 요청이 나감"


# ── 다운로드 ──────────────────────────────────────────────────────


def test_download_returns_bytes() -> None:
    assert download_image("https://images.pexels.com/x.jpeg", opener=opener_for(b"JPEGBYTES")) == (
        b"JPEGBYTES"
    )


def test_download_rejects_non_pexels_host() -> None:
    """spec에 실려 온 URL이 임의 호스트를 때리는 SSRF 통로가 되지 않게."""
    with pytest.raises(PexelsError, match="호스트"):
        download_image("https://evil.example.com/x.jpeg", opener=opener_for(b"x"))


def test_download_rejects_plain_http() -> None:
    with pytest.raises(PexelsError, match="https"):
        download_image("http://images.pexels.com/x.jpeg", opener=opener_for(b"x"))


def test_download_rejects_oversized_body() -> None:
    with pytest.raises(PexelsError, match="크기"):
        download_image(
            "https://images.pexels.com/x.jpeg",
            opener=opener_for(b"x" * (MAX_IMAGE_BYTES + 1)),
        )
