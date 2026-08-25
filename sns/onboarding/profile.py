"""채널 프로필 — 온보딩 인터뷰 산출물의 정본 파서·검증 (FR-W2 구체화).

인터뷰 6화면의 답이 여기 모인다: 주제 대분류 → 세부 주제(≤3) → 톤 → 목표(goal
프리셋) → 캐릭터 스타일 (+ 추천안·미세조정 줄글 원문 보존). 저장은 `channel_profile`
테이블의 jsonb 한 칸([sns.onboarding.store])이고, 이 모듈이 그 구조의 단일 출처다.

검증 규율은 render spec 파서와 동형 — LLM(refine)·폼 입력이 만든 값은 저장 전에
여기서 거부된다. 닫힌 집합(톤·캐릭터 스타일·goal_ref)은 목록 밖이면 `ProfileError`.
주제 대분류·세부 주제는 "직접 입력" 선택지가 있으므로 자유 텍스트를 허용한다.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from sns.goals import resolve_goal
from sns.topic_policy import DEV_CATEGORIES, GENERIC_CATEGORIES, categories_for

PROFILE_VERSION = 1
MAX_TOPIC_SUBS = 3

# 닫힌 집합: 톤(화면 3) — key는 저장값, value는 화면 라벨.
TONES: dict[str, str] = {
    "professional": "전문적",
    "casual": "자유롭고 가벼운",
    "humor": "유머 중심",
    "story": "동기부여·스토리",
}

# 닫힌 집합: 캐릭터 비주얼 스타일(화면 5). "none" = 캐릭터 미사용.
CHARACTER_STYLES: dict[str, str] = {
    "mascot_3d": "3D 마스코트",
    "flat_vector": "플랫 벡터",
    "pixel_art": "픽셀아트",
    "watercolor": "수채화",
    "none": "캐릭터 없음",
}

# 인터뷰 화면 1·2의 제시 목록(직접 입력 허용 — 강제 아님).
TOPIC_MAJORS: dict[str, tuple[str, ...]] = {
    "개발": ("AI", "파이썬", "자바", "웹", "커리어"),
    "요리": ("자취요리", "베이킹", "비건", "한식", "간편식"),
    "음악": ("K-POP", "작곡", "악기", "플레이리스트", "공연"),
    "춤": ("K-POP 안무", "스트릿댄스", "발레", "튜토리얼", "챌린지"),
}

# topic 카테고리 5종은 [sns.topic_policy]로 옮겼다 — 렌더·에이전트도 같은 분기를 읽어야
# 하는데, 온보딩 쪽에 두면 렌더가 온보딩에 의존하게 되어 방향이 거꾸로 선다.
# 기존 import 경로(`from sns.onboarding.profile import categories_for`)를 위해 재수출한다.
__all__ = [
    "CHARACTER_STYLES",
    "DEV_CATEGORIES",
    "GENERIC_CATEGORIES",
    "MAX_TOPIC_SUBS",
    "PROFILE_VERSION",
    "TONES",
    "TOPIC_MAJORS",
    "ChannelProfile",
    "ProfileError",
    "build_channel_brief",
    "categories_for",
    "parse_profile",
    "profile_to_json",
]


class ProfileError(ValueError):
    """프로필 구조·값 오류 — 저장 진입 전 차단."""


@dataclass(frozen=True)
class ChannelProfile:
    topic_major: str
    topic_subs: tuple[str, ...]
    tone: str
    goal_ref: str
    character_style: str
    categories: tuple[str, ...]
    character_image_url: str | None = None
    character_checksum: str | None = None
    # 추천안 원문(관측·회고용)과 미세조정 줄글 원문 — 검증 통과본과 함께 보존.
    recommendation: Mapping[str, object] | None = None
    note: str | None = None
    version: int = PROFILE_VERSION


def parse_profile(raw: object) -> ChannelProfile:
    """jsonb/폼/LLM 산출 dict → 검증된 ChannelProfile. 오류는 전부 ProfileError."""
    if not isinstance(raw, Mapping):
        raise ProfileError(f"프로필은 객체여야 한다: {type(raw).__name__}")

    topic_major = _require_text(raw, "topic_major")
    topic_subs = _parse_subs(raw.get("topic_subs"))

    tone = _require_text(raw, "tone")
    if tone not in TONES:
        raise ProfileError(f"알 수 없는 tone: {tone!r} (허용: {', '.join(TONES)})")

    goal_ref = _require_text(raw, "goal_ref")
    try:
        resolve_goal(goal_ref)
    except ValueError as e:
        raise ProfileError(str(e)) from e

    character = raw.get("character")
    if character is None:
        character = {}
    if not isinstance(character, Mapping):
        raise ProfileError("character는 객체여야 한다")
    style = character.get("style", "none")
    if not isinstance(style, str) or style not in CHARACTER_STYLES:
        raise ProfileError(
            f"알 수 없는 캐릭터 스타일: {style!r} (허용: {', '.join(CHARACTER_STYLES)})"
        )

    categories_raw = raw.get("categories")
    if categories_raw is None:
        categories = categories_for(topic_major)
    else:
        categories = _parse_text_tuple(categories_raw, "categories", max_items=8)

    recommendation = raw.get("recommendation")
    if recommendation is not None and not isinstance(recommendation, Mapping):
        raise ProfileError("recommendation은 객체여야 한다")

    return ChannelProfile(
        topic_major=topic_major,
        topic_subs=topic_subs,
        tone=tone,
        goal_ref=goal_ref,
        character_style=style,
        categories=categories,
        character_image_url=_optional_text(character, "image_url"),
        character_checksum=_optional_text(character, "checksum"),
        recommendation=None if recommendation is None else dict(recommendation),
        note=_optional_text(raw, "note"),
    )


def profile_to_json(profile: ChannelProfile) -> dict[str, object]:
    """저장용 dict — parse_profile과 왕복 가능(정본 직렬화)."""
    return {
        "version": profile.version,
        "topic_major": profile.topic_major,
        "topic_subs": list(profile.topic_subs),
        "tone": profile.tone,
        "goal_ref": profile.goal_ref,
        "categories": list(profile.categories),
        "character": {
            "style": profile.character_style,
            "image_url": profile.character_image_url,
            "checksum": profile.character_checksum,
        },
        "recommendation": None if profile.recommendation is None else dict(profile.recommendation),
        "note": profile.note,
    }


def build_channel_brief(profile: ChannelProfile) -> str:
    """프로필 → 에이전트 주입용 지침 텍스트.

    소비처 둘: `run_topic(guidance=...)`(주제 범위)과 `run_content(playbook_guidance=...)`
    (톤·캐릭터). 한 문단으로 합쳐도 두 에이전트 모두 자기 몫만 읽는다.
    """
    lines = [
        f"이 채널의 주제 범위: {profile.topic_major}"
        f" (세부: {', '.join(profile.topic_subs)}). 이 범위 밖 주제는 다루지 않는다.",
        f"콘텐츠 톤: {TONES[profile.tone]}. 모든 문장을 이 톤으로 쓴다.",
    ]
    if profile.character_style != "none":
        lines.append(
            f"이 채널은 {CHARACTER_STYLES[profile.character_style]} 스타일의 고정"
            " 캐릭터(마스코트)가 등장하는 컨셉이다."
        )
    if profile.note:
        lines.append(f"운영자 추가 지침: {profile.note}")
    return "\n".join(lines)


def _require_text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{key}는 비어 있지 않은 문자열이어야 한다: {value!r}")
    return value.strip()


def _optional_text(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProfileError(f"{key}는 문자열이어야 한다: {value!r}")
    stripped = value.strip()
    return stripped or None


def _parse_subs(raw: object) -> tuple[str, ...]:
    subs = _parse_text_tuple(raw, "topic_subs", max_items=MAX_TOPIC_SUBS)
    if len(set(subs)) != len(subs):
        raise ProfileError(f"topic_subs에 중복이 있다: {subs}")
    return subs


def _parse_text_tuple(raw: object, key: str, *, max_items: int) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        raise ProfileError(f"{key}는 문자열 목록이어야 한다: {raw!r}")
    items = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ProfileError(f"{key} 항목은 비어 있지 않은 문자열이어야 한다: {item!r}")
        items.append(item.strip())
    if not items:
        raise ProfileError(f"{key}는 최소 1개 필요하다")
    if len(items) > max_items:
        raise ProfileError(f"{key}는 최대 {max_items}개다: {len(items)}개")
    return tuple(items)
