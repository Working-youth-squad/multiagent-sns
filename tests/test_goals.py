import pytest

from sns.goals import DEFAULT_GOAL_REF, GOAL_PRESETS, resolve_goal

# 11-데이터모델 §4 metric_value.metric_key 표준 (신호 문자열은 이 집합의 부분집합이어야 함)
_IG_METRIC_KEYS = {
    "reach",
    "likes",
    "shares",
    "saved",
    "comments",
    "avg_watch_time_ms",
    "skip_rate",
    "views",
}
_YT_METRIC_KEYS = {
    "views",
    "engaged_views",
    "avg_view_duration_s",
    "avg_view_pct",
    "likes",
    "comments",
    "shares",
    "subscribers_gained",
}


def test_all_presets_self_consistent() -> None:
    # 딕셔너리 키와 프리셋의 ref가 일치 (등록 실수 방지)
    for ref, preset in GOAL_PRESETS.items():
        assert preset.ref == ref
        assert preset.label
        assert preset.ig_signals and preset.yt_signals


def test_four_presets_cover_undecided_axes() -> None:
    # 13-로드맵 §4: 조회수/팔로워/저장/시청유지
    assert set(GOAL_PRESETS) == {
        "reach_growth",
        "follower_growth",
        "engagement_depth",
        "watch_through",
    }


def test_signals_are_valid_metric_keys() -> None:
    # 신호 문자열이 metric_key 표준 밖이면 FR-A1 스코어보드 조인이 조용히 깨진다.
    # 오타("save" 등)를 등록 시점에 잡는다 (11-데이터모델 §4).
    for preset in GOAL_PRESETS.values():
        assert set(preset.ig_signals) <= _IG_METRIC_KEYS, preset.ref
        assert set(preset.yt_signals) <= _YT_METRIC_KEYS, preset.ref


def test_default_goal_is_registered() -> None:
    assert DEFAULT_GOAL_REF in GOAL_PRESETS


def test_resolve_known_goal() -> None:
    assert resolve_goal("watch_through").label == "시청 유지·완주"


def test_resolve_unknown_goal_rejected() -> None:
    with pytest.raises(ValueError, match="알 수 없는 goal_ref"):
        resolve_goal("viral_growth")
