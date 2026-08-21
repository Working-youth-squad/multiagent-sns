"""개발자 뉴스 소스 2종 — Hacker News · Lobsters (무인증).

**왜 붙였나.** 기존 무인증 소스 둘은 IT 주제 공급원으로 약하다:

- `google_trends`는 한국 **일반** 트렌드라 야구·만평·연예인이 올라온다. IT가 거의 없다.
- `github_trending`은 같은 저장소를 며칠씩 노출한다. 실제로 이틀 연속 같은 영상이 나갔다.

HN과 Lobsters는 키가 필요 없고, 하루에도 여러 번 바뀌며, 개발자 주제 밀도가 높다.
Reddit도 후보였지만 무인증 요청이 403으로 막힌다(실측) — OAuth가 필요해 뺐다.

두 소스 모두 **제목만** 뽑는다. 주제 후보로 쓰기에 제목이면 충분하고, 본문까지 끌어오면
응답이 커지고 파싱 실패 지점만 늘어난다. `github_trending`이 세운 규율을 따른다:
순수 파서 + 얇은 fetch + 주입 opener, 파싱 실패는 예외로 전파(서비스가 소스만 격리).
"""

import json
from collections.abc import Iterable

from sns.net.http import DEFAULT_OPENER, MAX_RESPONSE_BYTES, Opener, fetch_bytes

HN_URL = "https://hn.algolia.com/api/v1/search"
LOBSTERS_URL = "https://lobste.rs/hottest.json"
# 앞면에 갓 올라온 저점수 글은 트렌드가 아니다 — 주제로 쓰면 아무도 모르는 얘기가 된다.
MIN_HN_POINTS = 100
TIMEOUT_S = 10.0
# Lobsters는 UA 없는 요청을 차단하고, Algolia도 명시하는 편이 안전하다.
USER_AGENT = "multiagent-sns/0.1 (+https://github.com/Working-youth-squad/multiagent-sns)"


def _ordered_unique(titles: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for title in titles:
        clean = title.strip()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return tuple(out)


def parse_hacker_news(data: bytes) -> tuple[str, ...]:
    """Algolia 앞면 검색 응답 → 제목 목록. 점수 하한 미만은 버린다."""
    payload = json.loads(data)
    hits = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(hits, list):
        raise ValueError(f"HN 응답에 hits 배열이 없음: {str(payload)[:120]}")
    return _ordered_unique(
        str(h["title"])
        for h in hits
        if isinstance(h, dict)
        and isinstance(h.get("title"), str)
        and int(h.get("points") or 0) >= MIN_HN_POINTS
    )


def parse_lobsters(data: bytes) -> tuple[str, ...]:
    """Lobsters hottest 응답 → 제목 목록."""
    payload = json.loads(data)
    if not isinstance(payload, list):
        raise ValueError(f"Lobsters 응답이 배열이 아님: {str(payload)[:120]}")
    return _ordered_unique(
        str(s["title"]) for s in payload if isinstance(s, dict) and isinstance(s.get("title"), str)
    )


def _request(url: str) -> object:
    import urllib.request

    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def fetch_hacker_news(
    limit: int, *, timeout_s: float = TIMEOUT_S, opener: Opener = DEFAULT_OPENER
) -> tuple[str, ...]:
    data = fetch_bytes(
        _request(f"{HN_URL}?tags=front_page"),
        timeout_s=timeout_s,
        opener=opener,
        max_bytes=MAX_RESPONSE_BYTES,
    )
    return parse_hacker_news(data)[:limit]


def fetch_lobsters(
    limit: int, *, timeout_s: float = TIMEOUT_S, opener: Opener = DEFAULT_OPENER
) -> tuple[str, ...]:
    data = fetch_bytes(
        _request(LOBSTERS_URL),
        timeout_s=timeout_s,
        opener=opener,
        max_bytes=MAX_RESPONSE_BYTES,
    )
    return parse_lobsters(data)[:limit]
