"""저장된 관측 되먹이기 (M6 공용) — reward·분석글이 같은 값을 보게 하는 조각.

핵심은 둘이다: **네트워크를 타지 않는다**(analyst가 API를 다시 때리지 않는다), 그리고
**없는 것을 결측으로 둔갑시키지 않는다**(미폴링과 API 무응답은 다른 사건이다).
"""

from datetime import UTC, datetime, timedelta

import pytest

from sns.learning.observations import (
    StoredMetrics,
    UnknownObservation,
    as_metric_map,
)
from sns.learning.stores import InMemoryMetricStore, PublishedItem
from sns.tools.contracts import MetricValue, Platform

T0 = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)


def _item(pub_id: str, *, post_id: str, platform: Platform = "youtube") -> PublishedItem:
    return PublishedItem(
        publication_id=pub_id,
        platform=platform,
        external_post_id=post_id,
        published_at=T0,
        content_format="shorts" if platform == "youtube" else "reels",
        topic_id="topic-1",
        channel_mode="auto",
    )


def _store_with(*, window: int = 2) -> InMemoryMetricStore:
    store = InMemoryMetricStore()
    store.add_published_item(_item("pub-1", post_id="vid-1"))
    store.add_published_item(_item("pub-2", post_id="vid-2"))
    store.save_observation(
        publication_id="pub-1",
        window_index=window,
        values=[MetricValue("views", 120.0, False), MetricValue("avg_view_pct", None, True)],
    )
    return store


# ── as_metric_map ───────────────────────────────────────────────────


def test_missing_stays_as_a_none_key_not_a_dropped_key() -> None:
    """키를 빼면 '그 지표를 안 본다'가 되고, None이면 '봤는데 안 줬다'가 된다."""
    values = (MetricValue("views", 3.0, False), MetricValue("shares", None, True))
    assert as_metric_map(values) == {"views": 3.0, "shares": None}


# ── PollMetrics 되먹이기 ────────────────────────────────────────────


def test_reads_stored_values_without_touching_the_network() -> None:
    stored = StoredMetrics(_store_with())
    assert stored("youtube", "vid-1", 2) == (
        MetricValue("avg_view_pct", None, True),
        MetricValue("views", 120.0, False),
    )


def test_metrics_of_gives_the_scoreboard_shape() -> None:
    stored = StoredMetrics(_store_with())
    assert stored.metrics_of("youtube", "vid-1", 2) == {"views": 120.0, "avg_view_pct": None}


def test_unpolled_window_raises_instead_of_faking_missing() -> None:
    stored = StoredMetrics(_store_with(window=2))
    with pytest.raises(UnknownObservation):
        stored("youtube", "vid-1", 0)  # 6h 창은 찍힌 적이 없다.


def test_unknown_post_raises() -> None:
    stored = StoredMetrics(_store_with())
    with pytest.raises(UnknownObservation):
        stored("youtube", "vid-없음", 2)


def test_platform_is_part_of_the_key() -> None:
    """post_id는 플랫폼 안에서만 유일하다 — 같은 문자열이 IG에도 있을 수 있다."""
    stored = StoredMetrics(_store_with())
    with pytest.raises(UnknownObservation):
        stored("instagram", "vid-1", 2)


# ── 표본 고르기 ─────────────────────────────────────────────────────


def test_available_posts_lists_only_polled_ones() -> None:
    stored = StoredMetrics(_store_with(window=2))
    assert stored.available_posts("youtube", window_index=2) == ("vid-1",)
    assert stored.available_posts("youtube", window_index=0) == ()


def test_available_posts_is_the_filter_that_makes_calls_safe() -> None:
    """호출부 관례: 먼저 거르고 부르면 예외를 만날 일이 없다."""
    stored = StoredMetrics(_store_with(window=2))
    posts = stored.available_posts("youtube", window_index=2)
    assert [stored.metrics_of("youtube", p, 2)["views"] for p in posts] == [120.0]


def test_index_is_frozen_at_construction() -> None:
    """분석 도중 원장이 자라도 같은 표본을 본다 — 결정론(NFR-2)."""
    store = _store_with()
    stored = StoredMetrics(store)
    store.add_published_item(_item("pub-3", post_id="vid-3"))
    store.save_observation(
        publication_id="pub-3", window_index=2, values=[MetricValue("views", 9.0, False)]
    )
    with pytest.raises(UnknownObservation):
        stored("youtube", "vid-3", 2)


def test_since_narrows_the_index() -> None:
    store = InMemoryMetricStore()
    store.add_published_item(_item("pub-old", post_id="vid-old"))
    fresh = PublishedItem(
        publication_id="pub-new",
        platform="youtube",
        external_post_id="vid-new",
        published_at=T0 + timedelta(days=10),
        content_format="shorts",
        topic_id="topic-1",
        channel_mode="auto",
    )
    store.add_published_item(fresh)
    store.save_observation(
        publication_id="pub-new", window_index=2, values=[MetricValue("views", 5.0, False)]
    )
    stored = StoredMetrics(store, since=T0 + timedelta(days=5))
    assert stored.available_posts("youtube", window_index=2) == ("vid-new",)
    with pytest.raises(UnknownObservation):
        stored("youtube", "vid-old", 2)
