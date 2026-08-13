"""스코어보드(FR-A1·A4) — 비율·결측·기준선·소표본 게이트."""

import json

from sns.signals.scoreboard import (
    MIN_BASELINE_N,
    MIN_REGRESSION_N,
    VARIANCE_WARNING,
    PostSignals,
    common_elements,
    compute_scoreboard,
    scoreboard_json,
    signal_values,
)


def _yt(post_id: str, views: float | None, engaged: float | None, likes: float = 1.0):
    metrics = {
        "views": views,
        "engaged_views": engaged,
        "likes": likes,
        "avg_view_pct": 50.0,
        "avg_view_duration_s": 10.0,
        "subscribers_gained": 0.0,
    }
    return PostSignals(post_id=post_id, values=signal_values("youtube", metrics))


def test_ratio_and_missing_and_zero_denominator() -> None:
    values = signal_values("youtube", {"views": 1000.0, "engaged_views": 800.0, "likes": 50.0})
    assert values["engaged_rate"] == 0.8
    assert values["likes_per_view"] == 0.05
    assert values["avg_view_pct"] is None  # 결측 → None, 0 아님
    zero = signal_values("youtube", {"views": 0.0, "engaged_views": 0.0, "likes": 0.0})
    assert zero["engaged_rate"] is None  # 분모 0 → None


def test_instagram_signal_defs() -> None:
    values = signal_values("instagram", {"shares": 4.0, "reach": 100.0, "likes": 10.0})
    assert values["sends_per_reach"] == 0.04
    assert values["likes_per_reach"] == 0.1
    assert values["saves_per_reach"] is None


def test_small_baseline_gives_no_verdict() -> None:
    target = _yt("t", 1000, 800)
    others = [_yt(f"p{i}", 1000, 500) for i in range(MIN_BASELINE_N - 1)]
    sb = compute_scoreboard("youtube", target, others, window_index=0)
    assert not sb.verdict_available
    assert all(r.tag == "no_verdict" for r in sb.rows)
    assert sb.variance_warning == VARIANCE_WARNING  # 상시 포함 (FR-A4)


def test_baseline_median_and_tagging() -> None:
    target = _yt("t", 1000, 900)  # engaged_rate 0.9
    others = [_yt(f"p{i}", 1000, engaged) for i, engaged in enumerate([500, 600, 700, 800, 850])]
    sb = compute_scoreboard("youtube", target, others, window_index=1)
    assert sb.verdict_available and sb.small_sample  # 5 <= n < 30
    row = next(r for r in sb.rows if r.name == "engaged_rate")
    assert row.baseline == 0.7  # 중앙값
    assert row.tag == "above"


def test_missing_posts_do_not_poison_baseline() -> None:
    target = _yt("t", 1000, 100)
    # 유효 5건 + 결측(views=None) 3건 — 결측은 중앙값에서 제외
    others = [_yt(f"p{i}", 1000, 500) for i in range(5)] + [
        _yt(f"m{i}", None, None) for i in range(3)
    ]
    sb = compute_scoreboard("youtube", target, others, window_index=0)
    row = next(r for r in sb.rows if r.name == "engaged_rate")
    assert row.baseline == 0.5
    assert row.tag == "below"


def test_small_sample_flag_off_at_30() -> None:
    target = _yt("t", 1000, 800)
    others = [_yt(f"p{i}", 1000, 500) for i in range(MIN_REGRESSION_N)]
    sb = compute_scoreboard("youtube", target, others, window_index=0)
    assert not sb.small_sample


def test_common_elements_majority_and_floor() -> None:
    posts = [
        {"hook_pattern": "question", "topic": "ai"},
        {"hook_pattern": "question", "topic": "ai"},
        {"hook_pattern": "question", "topic": "dev"},
        {"hook_pattern": "story", "topic": "dev"},
        {"hook_pattern": "question", "topic": "etc"},
    ]
    assert common_elements(posts) == {"hook_pattern": "question"}  # topic은 과반 없음
    assert common_elements(posts[:2]) is None  # floor 미만 → 판정 불가


def test_scoreboard_json_roundtrip() -> None:
    target = _yt("t", 1000, 800)
    sb = compute_scoreboard("youtube", target, [], window_index=0)
    data = json.loads(scoreboard_json(sb))
    assert data["post_id"] == "t"
    assert data["verdict_available"] is False
    assert len(data["rows"]) == 5
