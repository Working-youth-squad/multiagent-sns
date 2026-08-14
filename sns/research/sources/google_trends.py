"""Google Trends 한국 급상승 — 무인증 RSS (04-트렌드조사 §2 소스 #1).

파싱(`parse_trends_rss`)은 순수 함수라 픽스처 바이트로 테스트한다. 네트워크
접촉은 얇은 `fetch_google_trends`에 격리하고, 소켓 타임아웃으로 소스별 10초
상한(§2)을 직접 강제한다 — 서비스 레벨 타임아웃의 이중 방어.
"""

import urllib.request
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol
from xml.etree import ElementTree as ET

# 지역=KR 일일 급상승 트렌드 RSS.
TRENDS_RSS_URL = "https://trends.google.com/trending/rss?geo=KR"

# 외부 응답 크기 상한 — 악의적/오작동 소스의 메모리·파싱 DoS 방어(RSS는 실제 수십 KB).
MAX_RESPONSE_BYTES = 5_000_000


class _Response(Protocol):
    def read(self, amt: int = ..., /) -> bytes: ...


# urllib 계열 opener 주입점(테스트에서 네트워크 대신 픽스처 응답 주입).
Opener = Callable[..., AbstractContextManager[_Response]]


def parse_trends_rss(data: bytes) -> tuple[str, ...]:
    """RSS 바이트에서 트렌드 키워드(<item><title>)를 순서대로 뽑는다.

    빈/공백 title은 제외한다. 잘못된 XML은 `ET.ParseError`로 던진다 —
    호출부(서비스)가 소스 격리로 처리한다.
    """
    root = ET.fromstring(data)
    titles = (item.findtext("title") or "" for item in root.iter("item"))
    return tuple(t.strip() for t in titles if t.strip())


def fetch_google_trends(
    limit: int,
    *,
    url: str = TRENDS_RSS_URL,
    timeout_s: float = 10.0,
    opener: Opener = urllib.request.urlopen,
) -> tuple[str, ...]:
    """상위 트렌드 키워드 최대 limit개. 네트워크/파싱 실패는 예외로 전파."""
    with opener(url, timeout=timeout_s) as resp:
        data = resp.read(MAX_RESPONSE_BYTES)
    return parse_trends_rss(data)[:limit]
