"""네이버 검색 API (뉴스) — 무료 25k/일 (04-트렌드조사 §2 소스 #2).

인증(X-Naver-Client-Id/Secret) + 질의어 시드로 최신 뉴스 제목을 뽑는다. 제목의 `<b>`
강조 태그·HTML 엔티티는 제거한다. 자격증명은 `default_service`가 env에서 바인딩 —
키 부재 시 아예 미등록이라 호출돼도 서비스가 ok=False로 격리한다.
"""

import html
import json
import re
import urllib.parse
import urllib.request

from sns.research.sources._http import DEFAULT_OPENER, MAX_RESPONSE_BYTES, Opener

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
_TAG_RE = re.compile(r"<[^>]+>")


def parse_naver_search(data: bytes) -> tuple[str, ...]:
    """JSON 응답에서 뉴스 제목(태그·엔티티 정리)을 순서대로 뽑는다. 빈 제목 제외."""
    payload = json.loads(data)
    titles = (
        html.unescape(_TAG_RE.sub("", item.get("title", ""))).strip()
        for item in payload.get("items", [])
    )
    return tuple(t for t in titles if t)


def fetch_naver_search(
    limit: int,
    *,
    client_id: str,
    client_secret: str,
    query: str = "개발자",
    url: str = NAVER_NEWS_URL,
    timeout_s: float = 10.0,
    opener: Opener = DEFAULT_OPENER,
) -> tuple[str, ...]:
    q = urllib.parse.urlencode({"query": query, "display": min(max(limit, 1), 100), "sort": "date"})
    request = urllib.request.Request(
        f"{url}?{q}",
        headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret},
    )
    with opener(request, timeout=timeout_s) as resp:
        data = resp.read(MAX_RESPONSE_BYTES)
    return parse_naver_search(data)[:limit]
