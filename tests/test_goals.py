import pytest

from sns.goals import DEFAULT_GOAL_REF, GOAL_PRESETS, resolve_goal


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


def test_default_goal_is_registered() -> None:
    assert DEFAULT_GOAL_REF in GOAL_PRESETS


def test_resolve_known_goal() -> None:
    assert resolve_goal("watch_through").label == "시청 유지·완주"


def test_resolve_unknown_goal_rejected() -> None:
    with pytest.raises(ValueError, match="알 수 없는 goal_ref"):
        resolve_goal("viral_growth")
