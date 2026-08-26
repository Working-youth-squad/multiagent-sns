"""캐릭터 생성 — 가짜 generate로 1회 생성·멱등·비용 통제 검증 (실 API 0회)."""

import pytest

from sns.onboarding.character import (
    SCENE_RULES,
    character_subject,
    ensure_character,
    make_scene_generate,
)
from sns.onboarding.profile import ChannelProfile, categories_for
from sns.render.images.generate import ImageGenerationError
from sns.render.storage import InMemoryMediaStore


def _profile(**overrides: object) -> ChannelProfile:
    base: dict[str, object] = {
        "topic_major": "요리",
        "topic_subs": ("비건",),
        "tone": "casual",
        "goal_ref": "reach_growth",
        "character_style": "watercolor",
        "categories": categories_for("요리"),
    }
    base.update(overrides)
    return ChannelProfile(**base)  # type: ignore[arg-type]


def test_generates_once_and_stamps_url() -> None:
    calls: list[str] = []

    def fake_generate(subject: str, **kwargs: object) -> bytes:
        calls.append(subject)
        return b"png-bytes"

    store = InMemoryMediaStore()
    profile = ensure_character(_profile(), store, generate=fake_generate)
    assert profile.character_image_url is not None
    assert profile.character_checksum is not None
    assert store.get(profile.character_image_url) == b"png-bytes"
    assert calls == [character_subject(_profile())]

    # 멱등: 이미 URL이 있으면 유료 호출 0회.
    again = ensure_character(profile, store, generate=fake_generate)
    assert again == profile
    assert len(calls) == 1


def test_skips_when_style_none() -> None:
    def fail_generate(subject: str, **kwargs: object) -> bytes:
        raise AssertionError("호출되면 안 된다")

    profile = ensure_character(
        _profile(character_style="none"), InMemoryMediaStore(), generate=fail_generate
    )
    assert profile.character_image_url is None


def test_generation_error_propagates() -> None:
    # 웹 앱이 잡아서 "캐릭터 없음"으로 계속한다 — 여기서는 전파만 확인.
    def broke(subject: str, **kwargs: object) -> bytes:
        raise ImageGenerationError("할당량 0")

    with pytest.raises(ImageGenerationError):
        ensure_character(_profile(), InMemoryMediaStore(), generate=broke)


def test_scene_generate_carries_reference_and_scene_rules() -> None:
    """장면 생성기는 캐릭터 앵커를 레퍼런스로, 장면 규칙을 화풍으로 넘긴다."""
    seen: dict[str, object] = {}

    def spy(subject: str, **kwargs: object) -> bytes:
        seen["subject"] = subject
        seen.update(kwargs)
        return b"scene"

    fn = make_scene_generate(b"anchor-png", generate=spy)
    assert fn("mascot riding a rocket") == b"scene"
    assert seen["subject"] == "mascot riding a rocket"
    assert seen["reference_png"] == b"anchor-png"
    assert seen["style_rules"] == SCENE_RULES
    assert any("reference image" in r for r in SCENE_RULES)
    assert any("no text" in r for r in SCENE_RULES)


def test_style_rules_passed_to_generate() -> None:
    seen: dict[str, object] = {}

    def spy(subject: str, **kwargs: object) -> bytes:
        seen.update(kwargs)
        return b"x"

    ensure_character(_profile(character_style="pixel_art"), InMemoryMediaStore(), generate=spy)
    rules = seen["style_rules"]
    assert isinstance(rules, tuple) and any("pixel art" in r for r in rules)
    assert any("no text" in r for r in rules)  # 글자 금지 규칙 유지


def test_scene_rules_cover_every_character_style() -> None:
    """스타일이 늘었는데 장면 규칙이 없으면 렌더가 KeyError로 죽는다."""
    from sns.onboarding.character import scene_rules_for
    from sns.onboarding.profile import CHARACTER_STYLES

    for style in CHARACTER_STYLES:
        assert scene_rules_for(style), style


def test_scene_rules_are_vertical_and_textless() -> None:
    """장면은 9:16 풀블리드이고 글자는 우리가 그린다(생성 모델의 글자는 뭉개진다)."""
    from sns.onboarding.character import scene_rules_for

    rules = " ".join(scene_rules_for("flat_vector"))
    assert "9:16" in rules
    assert "no text" in rules
    assert "square" not in rules, "캐릭터용 1:1 규칙이 새어 들어왔다"


def test_scene_rules_share_the_style_vocabulary() -> None:
    """같은 스타일이면 캐릭터와 장면이 같은 화풍 낱말을 쓴다 — 따로 놀지 않게."""
    from sns.onboarding.character import scene_rules_for

    assert "pixel art" in " ".join(scene_rules_for("pixel_art"))
    assert "watercolor" in " ".join(scene_rules_for("watercolor"))


def test_unknown_style_falls_back() -> None:
    """'캐릭터 없음'을 고른 채널도 장면 화풍은 있어야 한다."""
    from sns.onboarding.character import scene_rules_for

    assert scene_rules_for("none")
    assert scene_rules_for("존재하지않는스타일") == scene_rules_for("none")
