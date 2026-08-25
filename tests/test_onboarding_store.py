"""OnboardingStore — InMemory 순수 로직 + Pg 실 SQL(append-only 이력) 검증."""

import psycopg

from sns.onboarding.profile import ChannelProfile, categories_for
from sns.onboarding.store import InMemoryOnboardingStore, PgOnboardingStore


def _profile(note: str | None = None) -> ChannelProfile:
    return ChannelProfile(
        topic_major="개발",
        topic_subs=("AI",),
        tone="casual",
        goal_ref="engagement_depth",
        character_style="none",
        categories=categories_for("개발"),
        note=note,
    )


def test_inmemory_latest_is_last_saved() -> None:
    store = InMemoryOnboardingStore()
    ch = store.create_channel(platform="youtube", handle="onboard-1")
    assert store.latest_profile(ch) is None
    store.save_profile(ch, _profile())
    store.save_profile(ch, _profile(note="개정"))
    latest = store.latest_profile(ch)
    assert latest is not None and latest.note == "개정"
    assert len(store.profiles[ch]) == 2  # 이력 보존


def test_inmemory_created_channel_is_hybrid() -> None:
    store = InMemoryOnboardingStore()
    store.create_channel(platform="instagram", handle="onboard-2")
    assert store.list_channels()[0].mode == "hybrid"


def test_pg_create_channel_defaults_hybrid(db: psycopg.Connection) -> None:
    store = PgOnboardingStore(db)
    ch = store.create_channel(platform="youtube", handle="pg-onboard-1")
    rows = {c.channel_id: c for c in store.list_channels()}
    assert rows[ch].mode == "hybrid"
    assert rows[ch].handle == "pg-onboard-1"


def test_pg_profile_roundtrip_and_history(db: psycopg.Connection) -> None:
    store = PgOnboardingStore(db)
    ch = store.create_channel(platform="youtube", handle="pg-onboard-2")
    assert store.latest_profile(ch) is None

    store.save_profile(ch, _profile())
    store.save_profile(ch, _profile(note="개정"))

    latest = store.latest_profile(ch)
    assert latest is not None
    assert latest.note == "개정"
    assert latest.topic_subs == ("AI",)
    # append-only: 개정해도 행이 쌓인다(FR-W2 이력 보존).
    count = db.execute(
        "SELECT count(*) FROM channel_profile WHERE channel_id = %s", (ch,)
    ).fetchone()
    assert count is not None and count[0] == 2
