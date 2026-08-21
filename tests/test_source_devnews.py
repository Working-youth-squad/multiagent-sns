"""개발자 뉴스 소스 2종 (Hacker News · Lobsters) — 순수 파서 + 얇은 fetch, 네트워크 0.

**왜 붙였나.** google_trends는 한국 일반 트렌드라 야구·만평·연예인이 올라와 IT 주제가
거의 없고, github_trending은 같은 저장소를 며칠씩 노출한다(실제로 이틀 연속 같은 영상이
나갔다). HN과 Lobsters는 키가 필요 없고 하루에도 여러 번 바뀌며 개발자 주제 밀도가 높다.
"""

import json

import pytest

from sns.research.sources.devnews import (
    HN_URL,
    LOBSTERS_URL,
    MIN_HN_POINTS,
    fetch_hacker_news,
    fetch_lobsters,
    parse_hacker_news,
    parse_lobsters,
)


def hn(hits: list[dict[str, object]]) -> bytes:
    return json.dumps({"hits": hits}).encode()


def hit(title: str, points: int = 500, **over: object) -> dict[str, object]:
    return {"title": title, "points": points, "objectID": title, **over}


def lob(stories: list[dict[str, object]]) -> bytes:
    return json.dumps(stories).encode()


def opener_for(payload: bytes, *, seen: list[object] | None = None):  # type: ignore[no-untyped-def]
    from contextlib import contextmanager
    from io import BytesIO

    @contextmanager
    def _open(target: object, timeout: float = 0):  # type: ignore[no-untyped-def]
        if seen is not None:
            seen.append(target)
        yield BytesIO(payload)

    return _open


# ── Hacker News ───────────────────────────────────────────────────


def test_hn_parses_titles_in_order() -> None:
    payload = hn([hit("HTML Can Do That"), hit("Bun 1.4")])
    assert parse_hacker_news(payload) == ("HTML Can Do That", "Bun 1.4")


def test_hn_drops_low_signal_stories() -> None:
    """앞면에 갓 올라온 10점짜리는 트렌드가 아니다 — 주제로 쓰면 아무도 모르는 얘기가 된다."""
    payload = hn([hit("진짜 화제", 800), hit("갓 올라온 글", MIN_HN_POINTS - 1)])
    assert parse_hacker_news(payload) == ("진짜 화제",)


def test_hn_skips_entries_without_a_title() -> None:
    payload = hn([{"points": 900}, hit("멀쩡한 글")])
    assert parse_hacker_news(payload) == ("멀쩡한 글",)


def test_hn_deduplicates_preserving_order() -> None:
    payload = hn([hit("같은 글"), hit("다른 글"), hit("같은 글")])
    assert parse_hacker_news(payload) == ("같은 글", "다른 글")


def test_hn_malformed_payload_raises() -> None:
    """파싱 실패는 예외로 전파되고 서비스가 그 소스만 격리한다(FR-G4)."""
    with pytest.raises(ValueError):
        parse_hacker_news(b"not json")


def test_hn_fetch_sends_user_agent_and_respects_limit() -> None:
    seen: list[object] = []
    payload = hn([hit(f"글 {i}") for i in range(10)])
    result = fetch_hacker_news(3, opener=opener_for(payload, seen=seen))
    assert len(result) == 3
    [request] = seen
    assert request.full_url.startswith(HN_URL)  # type: ignore[attr-defined]
    assert request.headers["User-agent"]  # type: ignore[attr-defined]


# ── Lobsters ──────────────────────────────────────────────────────


def test_lobsters_parses_titles() -> None:
    payload = lob([{"title": "Announcing Rust 1.98.0", "score": 33}])
    assert parse_lobsters(payload) == ("Announcing Rust 1.98.0",)


def test_lobsters_skips_untitled() -> None:
    payload = lob([{"score": 10}, {"title": "멀쩡한 글", "score": 5}])
    assert parse_lobsters(payload) == ("멀쩡한 글",)


def test_lobsters_deduplicates() -> None:
    payload = lob([{"title": "같은 글"}, {"title": "같은 글"}])
    assert parse_lobsters(payload) == ("같은 글",)


def test_lobsters_malformed_payload_raises() -> None:
    with pytest.raises(ValueError):
        parse_lobsters(b"{}")


def test_lobsters_fetch_respects_limit() -> None:
    seen: list[object] = []
    payload = lob([{"title": f"글 {i}"} for i in range(10)])
    assert len(fetch_lobsters(4, opener=opener_for(payload, seen=seen))) == 4
    assert seen[0].full_url == LOBSTERS_URL  # type: ignore[attr-defined]


# ── 서비스 등록 ───────────────────────────────────────────────────


def test_registered_without_any_key() -> None:
    """키가 필요 없다 — 무인증 소스라 항상 등록돼야 한다."""
    from sns.research.trends import default_service

    service = default_service(env={})
    assert "hacker_news" in service.sources
    assert "lobsters" in service.sources
