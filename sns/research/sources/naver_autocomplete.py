"""네이버 검색 자동완성 — 연관 검색어 인기 순서열 (04-트렌드조사 §2 [신설]).

`ac.search.naver.com/nx/ac`는 무인증이다 — 네이버 검색 API 키(naver_search·naver_datalab이
쓰는 것)와 무관하게 항상 등록된다. 응답 형식(실측):
`{"items": [[ [["연관어"], ...], ... ]]}` — 중첩이 깊어 파서가 방어적으로 훑는다.
"""

import json

from sns.net.http import DEFAULT_OPENER, Opener, fetch_bytes
from sns.research.sources._autocomplete import clamp_limit, get_request, related_terms

NAVER_AC_URL = "https://ac.search.naver.com/nx/ac"


def parse_naver_autocomplete(data: bytes, *, query: str = "") -> tuple[str, ...]:
    """중첩 리스트에서 첫 문자열만 뽑아 순서대로. 형식이 다르면 ValueError."""
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise ValueError("네이버 자동완성 응답 형식이 다르다 — object 기대")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("네이버 자동완성 응답에 items가 없다")

    terms: list[str] = []
    for group in items:
        if not isinstance(group, list):
            continue
        for entry in group:
            # 실측 형태는 [["연관어"], ...] — 첫 원소가 문자열이면 그게 연관어다.
            if isinstance(entry, list) and entry and isinstance(entry[0], str):
                terms.append(entry[0])
            elif isinstance(entry, str):
                terms.append(entry)
    return related_terms(query, terms)


def fetch_naver_autocomplete(
    limit: int,
    *,
    query: str,
    url: str = NAVER_AC_URL,
    timeout_s: float = 10.0,
    opener: Opener = DEFAULT_OPENER,
) -> tuple[str, ...]:
    """네이버 검색 자동완성 연관어 최대 limit개(인기 순). 무인증."""
    params = {"q": query, "st": "100", "r_format": "json", "r_enc": "UTF-8", "q_enc": "UTF-8"}
    data = fetch_bytes(get_request(url, params), timeout_s=timeout_s, opener=opener)
    return parse_naver_autocomplete(data, query=query)[: clamp_limit(limit)]
