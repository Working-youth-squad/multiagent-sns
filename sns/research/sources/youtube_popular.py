"""YouTube 인기영상 — chart=mostPopular, 지역=KR (§2 소스 #4, 1unit/회).

API 키로 한국 인기영상 제목을 뽑는다. 키는 `default_service`가 env에서 바인딩 —
부재 시 미등록이라 서비스가 ok=False로 격리한다.
"""

import json
import urllib.parse
import urllib.request

from sns.research.sources._http import DEFAULT_OPENER, MAX_RESPONSE_BYTES, Opener

YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def parse_youtube_popular(data: bytes) -> tuple[str, ...]:
    """videos.list 응답에서 snippet.title을 순서대로 뽑는다. 빈 제목 제외."""
    payload = json.loads(data)
    titles = (
        (item.get("snippet", {}).get("title") or "").strip() for item in payload.get("items", [])
    )
    return tuple(t for t in titles if t)


def fetch_youtube_popular(
    limit: int,
    *,
    api_key: str,
    region_code: str = "KR",
    url: str = YOUTUBE_VIDEOS_URL,
    timeout_s: float = 10.0,
    opener: Opener = DEFAULT_OPENER,
) -> tuple[str, ...]:
    q = urllib.parse.urlencode(
        {
            "part": "snippet",
            "chart": "mostPopular",
            "regionCode": region_code,
            "maxResults": min(max(limit, 1), 50),
            "key": api_key,
        }
    )
    with opener(f"{url}?{q}", timeout=timeout_s) as resp:
        data = resp.read(MAX_RESPONSE_BYTES)
    return parse_youtube_popular(data)[:limit]
