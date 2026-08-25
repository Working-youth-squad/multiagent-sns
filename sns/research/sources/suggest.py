"""구글·유튜브 검색 자동완성 — 연관 검색어 인기 순서열 (04-트렌드조사 §2 [신설]).

`suggestqueries.google.com/complete/search`는 검색창 자동완성에 쓰이는 엔드포인트로,
질의어에 대해 **사용자들이 실제로 입력하는 연관 검색어**를 인기 순으로 돌려준다.
`ds=yt`면 유튜브 검색창의 자동완성이 된다.

기존 소스들과 결이 다른 점: 이들은 **질의어를 받는다**. 고정 피드(google_trends 등)는
`default_service()`에 그대로 등록되지만, 이 3종은 질의어가 정해져야 fetcher가 되므로
`sns.research.keywords.keyword_service(query)`가 배선한다.

비공식 엔드포인트라 예고 없이 형식·정책이 바뀔 수 있다. 실패는 예외로 던지고 서비스가
그 소스만 격리한다(FR-G4). 응답 형식(실측): `["질의어", [연관어…], …]`.
"""

import json

from sns.net.http import DEFAULT_OPENER, Opener, fetch_bytes
from sns.research.sources._autocomplete import clamp_limit, get_request, related_terms

SUGGEST_URL = "https://suggestqueries.google.com/complete/search"


def parse_suggest(data: bytes) -> tuple[str, ...]:
    """`["질의어", [연관어…], …]`에서 연관어를 순서대로. 형식이 다르면 ValueError."""
    payload = json.loads(data)
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        raise ValueError("자동완성 응답 형식이 다르다 — [질의어, [연관어…]] 기대")
    query = payload[0] if isinstance(payload[0], str) else ""
    return related_terms(query, payload[1])


def _fetch(
    limit: int,
    *,
    query: str,
    ds: str | None,
    url: str,
    hl: str,
    gl: str,
    timeout_s: float,
    opener: Opener,
) -> tuple[str, ...]:
    # oe=utf-8은 UA 스니핑에 기대지 않고 인코딩을 못 박는 쪽 — 둘 다 건다(_autocomplete 참조).
    params = {"client": "firefox", "hl": hl, "gl": gl, "oe": "utf-8", "q": query}
    if ds is not None:
        params["ds"] = ds
    data = fetch_bytes(get_request(url, params), timeout_s=timeout_s, opener=opener)
    return parse_suggest(data)[: clamp_limit(limit)]


def fetch_google_suggest(
    limit: int,
    *,
    query: str,
    url: str = SUGGEST_URL,
    hl: str = "ko",
    gl: str = "kr",
    timeout_s: float = 10.0,
    opener: Opener = DEFAULT_OPENER,
) -> tuple[str, ...]:
    """구글 검색 자동완성 연관어 최대 limit개(인기 순)."""
    return _fetch(
        limit, query=query, ds=None, url=url, hl=hl, gl=gl, timeout_s=timeout_s, opener=opener
    )


def fetch_youtube_suggest(
    limit: int,
    *,
    query: str,
    url: str = SUGGEST_URL,
    hl: str = "ko",
    gl: str = "kr",
    timeout_s: float = 10.0,
    opener: Opener = DEFAULT_OPENER,
) -> tuple[str, ...]:
    """유튜브 검색 자동완성 연관어 최대 limit개. 같은 엔드포인트의 `ds=yt`."""
    return _fetch(
        limit, query=query, ds="yt", url=url, hl=hl, gl=gl, timeout_s=timeout_s, opener=opener
    )
