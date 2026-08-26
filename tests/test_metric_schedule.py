"""폴링 창 스케줄 (FR-L1·A3) — 순수 함수라 DB도 API도 없이 전부 겨눌 수 있다.

핵심 판정 셋: 기한이 됐는가 · 이미 찍었는가 · **너무 늦지 않았는가**.
셋째가 이 모듈이 존재하는 이유다 — 늦은 창에 지금 값을 적으면 없던 곡선이 생긴다.
"""

from datetime import UTC, datetime, timedelta

import pytest

from sns.learning.schedule import (
    DEFAULT_HORIZON_DAYS,
    REWARD_WINDOW_INDEX,
    plan_windows,
    window_due_at,
    window_grace_hours,
    window_offset_hours,
)

PUBLISHED = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)


def _at(hours: float) -> datetime:
    return PUBLISHED + timedelta(hours=hours)


# ── 창 좌표 ─────────────────────────────────────────────────────────


def test_early_windows_are_the_spec_values() -> None:
    assert [window_offset_hours(i) for i in range(3)] == [6, 24, 72]


def test_later_windows_are_daily() -> None:
    assert [window_offset_hours(i) for i in (3, 4, 5)] == [96, 120, 144]


def test_negative_index_is_rejected() -> None:
    with pytest.raises(ValueError):
        window_offset_hours(-1)


def test_grace_never_reaches_the_next_window() -> None:
    """유예가 다음 창을 침범하면 두 창이 같은 시점을 가리킨다."""
    for index in range(6):
        span = window_offset_hours(index + 1) - window_offset_hours(index)
        assert window_grace_hours(index) < span


def test_due_at_is_published_plus_offset() -> None:
    assert window_due_at(PUBLISHED, 1) == _at(24)


# ── plan_windows ────────────────────────────────────────────────────


def test_nothing_is_due_before_the_first_window() -> None:
    assert plan_windows(published_at=PUBLISHED, now=_at(5)).due == ()


def test_first_window_becomes_due_on_time() -> None:
    assert plan_windows(published_at=PUBLISHED, now=_at(6)).due == (0,)


def test_at_most_one_window_is_due_at_a_time() -> None:
    """유예가 다음 창까지 거리의 절반이라, 한 번의 훑기가 두 창을 함께 찍는 일은 없다.

    창 하나가 여러 개로 불어나는 경로가 없다는 뜻이다 — 곡선을 지어낼 방법이 애초에 없다.
    """
    for hour in range(1, 200):
        assert len(plan_windows(published_at=PUBLISHED, now=_at(hour)).due) <= 1


def test_a_window_polled_within_grace_is_still_due() -> None:
    """6h 창의 유예는 9h — 14h에 발견해도 아직 그 창이다."""
    assert plan_windows(published_at=PUBLISHED, now=_at(14)).due == (0,)


def test_observed_windows_are_never_returned() -> None:
    plan = plan_windows(published_at=PUBLISHED, now=_at(25), observed=(0,))
    assert plan.due == (1,)
    assert plan.missed == ()  # 끝난 일은 놓친 것도 아니다.


def test_late_window_is_missed_not_polled() -> None:
    """6h 창의 유예는 9h — 20h에 발견하면 지금 값을 적지 않고 포기한다."""
    plan = plan_windows(published_at=PUBLISHED, now=_at(20))
    assert plan.due == ()
    assert plan.missed == (0,)


def test_long_outage_misses_early_windows_and_keeps_the_fresh_one() -> None:
    """하루 반 멈췄다 살아난 폴러: 6h·24h는 포기, 72h만 제때 찍는다."""
    plan = plan_windows(published_at=PUBLISHED, now=_at(73))
    assert plan.due == (2,)
    assert plan.missed == (0, 1)


def test_horizon_stops_the_daily_tail() -> None:
    now = _at(DEFAULT_HORIZON_DAYS * 24 + 48)
    plan = plan_windows(published_at=PUBLISHED, now=now)
    last = max(plan.due + plan.missed)
    assert window_offset_hours(last) <= DEFAULT_HORIZON_DAYS * 24


def test_shorter_horizon_narrows_the_plan() -> None:
    plan = plan_windows(published_at=PUBLISHED, now=_at(200), horizon_days=3)
    assert max(plan.due + plan.missed) == 2  # 72h가 마지막


def test_future_publication_plans_nothing() -> None:
    """시계 뒤틀림·예약 발행 — 음수 경과에서 창을 만들지 않는다."""
    assert plan_windows(published_at=PUBLISHED, now=_at(-3)) == plan_windows(
        published_at=PUBLISHED, now=_at(-100)
    )
    assert plan_windows(published_at=PUBLISHED, now=_at(-3)).due == ()


def test_reward_window_is_the_72h_one() -> None:
    """B(reward)와 C(분석글)가 같은 창을 본다는 약속 — 상수가 움직이면 여기서 걸린다."""
    assert window_offset_hours(REWARD_WINDOW_INDEX) == 72
