"""Google Trends RSS fetcher — 순수 파서 + opener 주입(네트워크 없음)."""

from xml.etree.ElementTree import ParseError

import pytest

from sns.research.sources.google_trends import fetch_google_trends, parse_trends_rss

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Daily Search Trends</title>
  <item><title>  손흥민  </title></item>
  <item><title>BTS 컴백</title></item>
  <item><title>   </title></item>
</channel></rss>""".encode()


class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.read_amt: int | None = None

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self, amt: int = -1) -> bytes:
        self.read_amt = amt
        return self._data


def _opener(data: bytes, sink: dict | None = None):
    def opener(url: str, timeout: float) -> _FakeResponse:
        if sink is not None:
            sink["url"] = url
            sink["timeout"] = timeout
        return _FakeResponse(data)

    return opener


def test_parse_extracts_stripped_nonempty_titles() -> None:
    assert parse_trends_rss(RSS) == ("손흥민", "BTS 컴백")  # 공백-only title 제외


def test_parse_empty_feed() -> None:
    assert parse_trends_rss(b"<rss><channel></channel></rss>") == ()


def test_parse_malformed_raises() -> None:
    with pytest.raises(ParseError):
        parse_trends_rss(b"<rss><channel><item>")


def test_fetch_parses_and_caps_limit() -> None:
    assert fetch_google_trends(1, opener=_opener(RSS)) == ("손흥민",)


def test_fetch_passes_url_and_timeout_and_bounds_read() -> None:
    sink: dict = {}
    resp_holder = _FakeResponse(RSS)

    def opener(url: str, timeout: float) -> _FakeResponse:
        sink["url"] = url
        sink["timeout"] = timeout
        return resp_holder

    out = fetch_google_trends(10, url="https://example/rss", timeout_s=3.0, opener=opener)
    assert out == ("손흥민", "BTS 컴백")
    assert sink == {"url": "https://example/rss", "timeout": 3.0}
    assert resp_holder.read_amt == 5_000_000  # 응답 크기 상한 적용
