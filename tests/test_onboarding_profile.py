"""ChannelProfile 파서·검증·직렬화 왕복 (순수, 네트워크·DB 없음)."""

import pytest

from sns.onboarding.profile import (
    DEV_CATEGORIES,
    GENERIC_CATEGORIES,
    ChannelProfile,
    ProfileError,
    build_channel_brief,
    categories_for,
    parse_profile,
    profile_to_json,
)


def _raw(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "topic_major": "개발",
        "topic_subs": ["AI", "파이썬"],
        "tone": "casual",
        "goal_ref": "engagement_depth",
        "character": {"style": "flat_vector"},
    }
    base.update(overrides)
    return base


def test_parse_roundtrip() -> None:
    profile = parse_profile(_raw(note="  좀 더 밝게  "))
    assert profile == parse_profile(profile_to_json(profile))
    assert profile.topic_subs == ("AI", "파이썬")
    assert profile.note == "좀 더 밝게"


def test_categories_derived_by_major() -> None:
    assert parse_profile(_raw()).categories == DEV_CATEGORIES
    assert parse_profile(_raw(topic_major="요리")).categories == GENERIC_CATEGORIES
    assert categories_for("음악") == GENERIC_CATEGORIES


def test_explicit_categories_kept() -> None:
    profile = parse_profile(_raw(categories=["레시피", "꿀팁"]))
    assert profile.categories == ("레시피", "꿀팁")


def test_character_defaults_to_none_style() -> None:
    raw = _raw()
    del raw["character"]
    assert parse_profile(raw).character_style == "none"


def test_character_url_and_checksum_preserved() -> None:
    profile = parse_profile(
        _raw(character={"style": "pixel_art", "image_url": "mem://abc", "checksum": "abc"})
    )
    assert profile.character_image_url == "mem://abc"
    assert profile.character_checksum == "abc"


@pytest.mark.parametrize(
    "overrides",
    [
        {"topic_major": ""},
        {"topic_subs": []},
        {"topic_subs": ["a", "b", "c", "d"]},  # 상한 3
        {"topic_subs": ["AI", "AI"]},  # 중복
        {"topic_subs": "AI"},  # 목록 아님
        {"tone": "냉소적"},  # 닫힌 집합 밖
        {"goal_ref": "go_viral"},  # 미등록 goal
        {"character": {"style": "3d"}},  # 닫힌 집합 밖
        {"categories": []},
        {"recommendation": "문자열"},
        {"note": 42},
    ],
)
def test_parse_rejects_bad_values(overrides: dict[str, object]) -> None:
    with pytest.raises(ProfileError):
        parse_profile(_raw(**overrides))


def test_parse_rejects_non_mapping() -> None:
    with pytest.raises(ProfileError):
        parse_profile(["not", "a", "mapping"])


def test_brief_contains_subs_tone_and_character() -> None:
    brief = build_channel_brief(parse_profile(_raw(note="이모지 활용")))
    assert "AI, 파이썬" in brief
    assert "자유롭고 가벼운" in brief
    assert "플랫 벡터" in brief
    assert "이모지 활용" in brief


def test_brief_omits_character_when_none() -> None:
    profile = parse_profile(_raw(character={"style": "none"}))
    assert "캐릭터" not in build_channel_brief(profile)


def test_dataclass_direct_construction() -> None:
    # 웹 계층이 폼 값으로 직접 만들 때의 최소 형태.
    profile = ChannelProfile(
        topic_major="요리",
        topic_subs=("비건",),
        tone="professional",
        goal_ref="reach_growth",
        character_style="none",
        categories=categories_for("요리"),
    )
    assert parse_profile(profile_to_json(profile)) == profile
