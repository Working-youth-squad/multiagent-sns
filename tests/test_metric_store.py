"""MetricStore 계약의 결정론 검증 — InMemory 구현으로 DB 없이 (M6, FR-L1~L4).

여기서 겨누는 것은 저장소의 SQL이 아니라 **계약이 약속한 행동**이다:
관측 멱등(같은 창은 한 번), reward 재계산이 topic_stats를 부풀리지 않을 것,
NULL 보상은 표본이 아닐 것, 플레이북이 scope별로 버전을 셀 것.
같은 시나리오를 Pg 구현에도 물려 두 구현이 갈리지 않게 한다(test_metric_store_pg).
"""

from datetime import UTC, datetime, timedelta

import pytest

from sns.learning.stores import InMemoryMetricStore, PublishedItem
from sns.tools.contracts import MetricValue

T0 = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


def _item(pub_id: str = "pub-1", *, offset_h: int = 0, topic_id: str = "topic-1") -> PublishedItem:
    return PublishedItem(
        publication_id=pub_id,
        platform="youtube",
        external_post_id=f"yt-{pub_id}",
        published_at=T0 + timedelta(hours=offset_h),
        content_format="shorts",
        topic_id=topic_id,
        channel_mode="auto",
    )


def _store(*items: PublishedItem) -> InMemoryMetricStore:
    store = InMemoryMetricStore()
    for item in items:
        store.add_published_item(item)
    return store


# ── published_items ─────────────────────────────────────────────────


def test_published_items_is_ordered_by_publish_time() -> None:
    store = _store(_item("pub-b", offset_h=5), _item("pub-a", offset_h=1))
    assert [i.publication_id for i in store.published_items()] == ["pub-a", "pub-b"]


def test_published_items_reports_observed_windows() -> None:
    """이미 찍은 창이 함께 와야 폴러가 같은 창을 다시 부르지 않는다(쿼터)."""
    store = _store(_item())
    store.save_observation(
        publication_id="pub-1", window_index=1, values=[MetricValue("views", 10.0, False)]
    )
    store.save_observation(
        publication_id="pub-1", window_index=0, values=[MetricValue("views", 3.0, False)]
    )
    assert store.published_items()[0].observed_windows == (0, 1)


def test_published_items_since_is_a_lower_bound() -> None:
    store = _store(_item("pub-old", offset_h=0), _item("pub-new", offset_h=48))
    picked = store.published_items(since=T0 + timedelta(hours=24))
    assert [i.publication_id for i in picked] == ["pub-new"]


# ── 관측 적재 ───────────────────────────────────────────────────────


def test_save_observation_is_idempotent_and_does_not_overwrite() -> None:
    store = _store(_item())
    first = store.save_observation(
        publication_id="pub-1", window_index=0, values=[MetricValue("views", 10.0, False)]
    )
    again = store.save_observation(
        publication_id="pub-1", window_index=0, values=[MetricValue("views", 999.0, False)]
    )
    assert first is not None
    assert again is None  # 흡수 — 재구동이 시계열을 뒤집지 않는다.
    assert store.read_observation(publication_id="pub-1", window_index=0) == (
        MetricValue("views", 10.0, False),
    )


def test_missing_metric_survives_roundtrip_as_null() -> None:
    """결측을 0으로 채우는 경로가 없어야 한다(NFR-3)."""
    store = _store(_item())
    store.save_observation(
        publication_id="pub-1",
        window_index=0,
        values=[MetricValue("views", 12.0, False), MetricValue("avg_view_pct", None, True)],
    )
    values = {
        v.metric_key: v for v in store.read_observation(publication_id="pub-1", window_index=0)
    }
    assert values["avg_view_pct"].value is None
    assert values["avg_view_pct"].missing is True


def test_metric_value_rejects_zero_filled_missing() -> None:
    with pytest.raises(ValueError):
        MetricValue("views", 0.0, True)


def test_read_observation_of_unpolled_window_is_empty() -> None:
    store = _store(_item())
    assert store.read_observation(publication_id="pub-1", window_index=2) == ()


# ── reward · topic_stats ────────────────────────────────────────────


def test_reward_bumps_topic_stats_once() -> None:
    store = _store(_item())
    store.save_reward(publication_id="pub-1", reward_value=0.5, formula_version="v1")
    stat = store.read_topic_stats()[0]
    assert (stat.trials, stat.reward_sum) == (1, 0.5)


def test_recompute_replaces_instead_of_accumulating() -> None:
    """같은 건을 다시 계산해도 trials는 1 — 중복 집계가 밴딧을 조용히 오염시킨다."""
    store = _store(_item())
    store.save_reward(publication_id="pub-1", reward_value=0.5, formula_version="v1")
    store.save_reward(publication_id="pub-1", reward_value=0.9, formula_version="v2")
    stat = store.read_topic_stats()[0]
    assert (stat.trials, stat.reward_sum) == (1, 0.9)


def test_null_reward_is_not_a_trial() -> None:
    store = _store(_item())
    store.save_reward(publication_id="pub-1", reward_value=None, formula_version="v1")
    assert store.read_topic_stats() == ()
    # 그래도 "봤다"는 사실은 남는다 — 미확정과 미계산은 다르다.
    assert store.read_reward("pub-1") == (None, "v1")


def test_reward_revoked_to_null_removes_the_trial() -> None:
    store = _store(_item())
    store.save_reward(publication_id="pub-1", reward_value=0.5, formula_version="v1")
    store.save_reward(publication_id="pub-1", reward_value=None, formula_version="v2")
    stat = store.read_topic_stats()[0]
    assert (stat.trials, stat.reward_sum) == (0, 0.0)


def test_unseen_publication_has_no_reward_row() -> None:
    store = _store(_item())
    assert store.read_reward("pub-1") is None


def test_stats_are_grouped_by_topic_format_platform() -> None:
    store = _store(_item("pub-1", topic_id="topic-1"), _item("pub-2", topic_id="topic-2"))
    store.save_reward(publication_id="pub-1", reward_value=0.4, formula_version="v1")
    store.save_reward(publication_id="pub-2", reward_value=0.6, formula_version="v1")
    assert {s.topic_id: s.reward_sum for s in store.read_topic_stats()} == {
        "topic-1": 0.4,
        "topic-2": 0.6,
    }


def test_read_topic_stats_filters_by_platform() -> None:
    store = _store(_item())
    store.save_reward(publication_id="pub-1", reward_value=0.4, formula_version="v1")
    assert store.read_topic_stats(platform="youtube")
    assert store.read_topic_stats(platform="instagram") == ()


# ── playbook · analysis_note · event ────────────────────────────────


def test_playbook_versions_count_per_scope() -> None:
    store = InMemoryMetricStore()
    assert store.save_playbook("global", "첫 지침").version == 1
    assert store.save_playbook("global", "둘째 지침").version == 2
    # scope가 다르면 버전도 따로 센다.
    assert store.save_playbook("platform", "유튜브 지침", "youtube").version == 1


def test_analysis_note_records_insufficient_evidence_flag() -> None:
    store = InMemoryMetricStore()
    store.save_analysis_note(cycle_id=None, body="근거 부족", insufficient_evidence=True)
    assert store.notes[0]["insufficient_evidence"] is True


def test_log_event_is_append_only() -> None:
    store = InMemoryMetricStore()
    store.log_event(cycle_id=None, kind="metric_polled", payload={"window_index": 0})
    store.log_event(cycle_id=None, kind="error", payload={"reason": "quota"})
    assert [e["kind"] for e in store.events] == ["metric_polled", "error"]
