"""채널 캐릭터(페르소나) 생성 — 온보딩 완료 시 1회, 이후 영구 재사용.

[sns.render.images.generate.generate_image]를 재사용하되 화풍을 인터뷰 선택지
(`character_style`)로 갈아 끼운다. 유료 API라 비용 통제가 이 모듈의 존재 이유다:

1. `character.image_url`이 이미 있으면 **호출 자체를 하지 않는다** — 재생성은
   프로필의 URL을 비운 명시적 경로로만 가능하다.
2. `style == "none"`이면 스킵.
3. 생성 실패(키 없음·429·게이트)는 예외로 올라가고, 웹 앱이 "캐릭터 없음"으로
   온보딩을 계속한다 — 실패가 인터뷰 완주를 막지 않는다.

저장은 MediaStore(content-addressed) — checksum·URL을 프로필에 박제해 렌더가
재사용한다(영상 합성 반영은 후속: 렌더러 코너 오버레이).
"""

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import replace

from sns.onboarding.profile import ChannelProfile
from sns.render.images.generate import generate_image
from sns.render.storage import MediaStore

# 화풍이 곧 인터뷰 선택지 — generate.py의 기본 STYLE_RULES(영상 정사각용) 대신 쓴다.
_COMMON_RULES: tuple[str, ...] = (
    "square 1:1 composition",
    "single mascot character, full body, front-facing",
    "friendly, memorable, consistent character design",
    "clean solid background",
    "no text, no letters, no numbers, no watermark, no logos",
)

_STYLE_RULES: dict[str, tuple[str, ...]] = {
    "mascot_3d": ("cute 3D rendered mascot", "soft studio lighting", *_COMMON_RULES),
    "flat_vector": ("flat vector illustration mascot", "simple geometric shapes", *_COMMON_RULES),
    "pixel_art": ("pixel art mascot", "16-bit retro game style", *_COMMON_RULES),
    "watercolor": ("watercolor illustration mascot", "soft pastel palette", *_COMMON_RULES),
}

GenerateImage = Callable[..., bytes]


def character_subject(profile: ChannelProfile) -> str:
    """생성 프롬프트의 주제부 — 채널 주제가 캐릭터 정체성에 스며들게."""
    return (
        f"{profile.topic_major} 주제({', '.join(profile.topic_subs)}) SNS 채널을 "
        "대표하는 마스코트 캐릭터"
    )


def ensure_character(
    profile: ChannelProfile,
    store: MediaStore,
    *,
    generate: GenerateImage = generate_image,
) -> ChannelProfile:
    """캐릭터 이미지가 없으면 1회 생성해 URL·checksum을 박제한 프로필을 돌려준다.

    이미 있으면(또는 style이 none이면) 유료 호출 없이 그대로 반환 — 멱등.
    """
    if profile.character_style == "none" or profile.character_image_url is not None:
        return profile
    style_rules: Sequence[str] = _STYLE_RULES[profile.character_style]
    data = generate(character_subject(profile), style_rules=style_rules)
    checksum = hashlib.sha256(data).hexdigest()
    url = store.put(data, checksum=checksum, kind="image", ext="png")
    return replace(profile, character_image_url=url, character_checksum=checksum)


def make_character_fn(
    store: MediaStore, *, generate: GenerateImage = generate_image
) -> Callable[[ChannelProfile], ChannelProfile]:
    """웹 앱 주입용 조립."""

    def fn(profile: ChannelProfile) -> ChannelProfile:
        return ensure_character(profile, store, generate=generate)

    return fn
