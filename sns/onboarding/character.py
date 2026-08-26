"""채널 캐릭터(페르소나) 생성 — 온보딩 완료 시 1회, 이후 영구 재사용.

[sns.render.images.generate.generate_image]를 재사용하되 화풍을 인터뷰 선택지
(`character_style`)로 갈아 끼운다. 유료 API라 비용 통제가 이 모듈의 존재 이유다:

1. `character.image_url`이 이미 있으면 **호출 자체를 하지 않는다** — 재생성은
   프로필의 URL을 비운 명시적 경로로만 가능하다.
2. `style == "none"`이면 스킵.
3. 생성 실패(키 없음·429·게이트)는 예외로 올라가고, 웹 앱이 "캐릭터 없음"으로
   온보딩을 계속한다 — 실패가 인터뷰 완주를 막지 않는다.

저장은 MediaStore(content-addressed) — checksum·URL을 프로필에 박제해 렌더가
재사용한다. 영상 반영은 두 트랙:
1. 코너 오버레이 — spec의 `character_ref`로 렌더러가 배지를 얹는다
   ([sns.render.video.renderer]).
2. 장면 생성 — `make_scene_generate`로 캐릭터 앵커를 레퍼런스 삼아 컷 이미지를
   생성한다(캐릭터 일관성). 말하는 아바타(HeyGen/D-ID)로의 업그레이드는
   `character_ref`를 읽는 별도 RenderMedia 구현을 주입하면 된다 — 계약이 seam이다.
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

# 장면용 규칙 — 캐릭터용(_COMMON_RULES)과 **다르다**. 저쪽은 1:1 마스코트 한 마리를
# 단색 배경에 세우는 규칙이라 풀블리드 장면에 그대로 쓸 수 없다.
_SCENE_COMMON: tuple[str, ...] = (
    "vertical 9:16 composition, full bleed",
    "single clear subject, uncluttered composition",
    "no text, no letters, no numbers, no watermark, no logos",
)

_SCENE_RULES: dict[str, tuple[str, ...]] = {
    "mascot_3d": ("cute 3D rendered scene", "soft studio lighting", *_SCENE_COMMON),
    "flat_vector": ("flat vector illustration scene", "simple geometric shapes", *_SCENE_COMMON),
    "pixel_art": ("pixel art scene", "16-bit retro game style", *_SCENE_COMMON),
    "watercolor": ("watercolor illustration scene", "soft pastel palette", *_SCENE_COMMON),
    "none": ("clean photographic scene", "natural light", *_SCENE_COMMON),
}


def scene_rules_for(character_style: str) -> tuple[str, ...]:
    """생성 장면 프롬프트에 붙는 고정 화풍([sns.render.video.gen]).

    **인터뷰가 고른 `character_style`을 그대로 쓴다.** 영상 화풍을 따로 물으면 사람이 같은
    질문에 두 번 답하게 되고, 두 답이 어긋나면 캐릭터와 배경이 따로 논다. 같은 스타일 키가
    같은 화풍 낱말("pixel art", "watercolor")을 쓰므로 한 채널의 캐릭터와 장면이 붙는다.

    화풍이 코드에 있는 게 요점이다([sns.render.images.generate.STYLE_RULES]와 같은 규율) —
    매 사이클 화풍이 흔들리면 모델 비교가 화풍 비교로 오염된다.

    "캐릭터 없음"을 고른 채널도 장면은 필요하므로 `none`이 폴백을 겸한다.
    """
    return _SCENE_RULES.get(character_style, _SCENE_RULES["none"])


GenerateImage = Callable[..., bytes]

# 캐릭터 레퍼런스 장면용 — 영상 정사각 규칙(밝은 배경·글자 없음)에 캐릭터 유지를 얹는다.
SCENE_RULES: tuple[str, ...] = (
    "feature the exact mascot character from the reference image as the main subject",
    "keep the character's design, colors and proportions identical to the reference",
    "square 1:1 composition",
    "clean white background",
    "minimal, no text, no letters, no numbers, no watermark, no logos",
)


# 게이트([sns.render.images.gate])가 영문만 받는다 — 고정 대분류의 영문 매핑.
_MAJOR_EN: dict[str, str] = {
    "개발": "programming and tech",
    "요리": "cooking and food",
    "음악": "music",
    "춤": "dance",
}


def character_subject(profile: ChannelProfile) -> str:
    """생성 프롬프트의 주제부 — **영문**이어야 한다.

    실사고: 한글 subject가 게이트("검색어는 영문이어야 함")에 막혀 캐릭터 생성이
    온보딩 내내 조용히 실패했다. 고정 대분류는 매핑, 직접 입력 주제는 영문이면
    그대로, 한글이면 일반 문구로 폴백한다.
    """
    major = profile.topic_major
    theme = _MAJOR_EN.get(major) or (major if major.isascii() else None)
    about = f" for a {theme} social media channel" if theme else " for a social media channel"
    return f"a cute original mascot character{about}"


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


def make_scene_generate(
    character_png: bytes, *, generate: GenerateImage = generate_image
) -> Callable[[str], bytes]:
    """캐릭터 앵커 이미지를 레퍼런스로 장면을 생성 — resolve_images의 GenerateImage 모양."""

    def fn(prompt: str) -> bytes:
        return generate(prompt, style_rules=SCENE_RULES, reference_png=character_png)

    return fn


def make_character_fn(
    store: MediaStore, *, generate: GenerateImage = generate_image
) -> Callable[[ChannelProfile], ChannelProfile]:
    """웹 앱 주입용 조립."""

    def fn(profile: ChannelProfile) -> ChannelProfile:
        return ensure_character(profile, store, generate=generate)

    return fn
