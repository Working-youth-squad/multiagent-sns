"""트렌드 기반 추천안 + 줄글 미세조정 — 온보딩 화면 6의 두뇌.

트렌드는 **탈부착 심**이다: `TrendProvider = 프로필 → TrendDigest` 호출자 주입.
기본 구현(`default_trend_provider`)은 [sns.onboarding.trends.profile_trend_service]로
이 채널 주제에 맞춘 소스·질의어를 조립한다 — 사이클도 같은 함수를 쓴다.
붙였다 떼어도 웹·프로필 코드는 무변경.

`recommend`의 근거 규율은 [sns.agents.topic]과 동형: `hot_trends`는 다이제스트에
실제로 있는 항목만 통과시킨다(할루시네이션 차단). 어떤 실패도 예외 대신 None —
추천은 온보딩을 절대 막지 않는다.

`refine`은 LLM에게 프로필 전체 JSON을 개정시키되, 결과를 `parse_profile`로 검증하고
캐릭터 이미지 URL·checksum과 추천안 원문은 **코드가 원본으로 되덮는다** — 줄글이
유료 재생성을 유발하거나 관측 기록을 지우는 경로를 차단한다.
"""

import json
import re
from collections.abc import Callable, Mapping

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from sns.onboarding.profile import (
    ChannelProfile,
    ProfileError,
    parse_profile,
    profile_to_json,
)
from sns.onboarding.trends import profile_trend_service
from sns.tools.contracts import TrendDigest

TrendProvider = Callable[[ChannelProfile], TrendDigest]

MAX_FOCUS_SUBS = 3
MAX_HOT_TRENDS = 5
MAX_NAME_IDEAS = 3
MAX_NAME_CHARS = 30
MAX_TUNE_IDEAS = 3
MAX_TUNE_CHARS = 80


def default_trend_provider(profile: ChannelProfile) -> TrendDigest:
    """기본 트렌드 조립 — **이 채널 주제에 맞춘** 다이제스트.

    조립은 [sns.onboarding.trends.profile_trend_service]가 한다 — 사이클도 같은 것을
    쓰므로 한 곳에만 둔다. 예전엔 여기서 `default_service()`를 그냥 불러 `profile`을
    받아놓고 쓰지 않았고, 그래서 요리 채널이 개발 트렌드를 근거로 이름을 추천받았다.
    """
    return profile_trend_service(profile)(limit=10)


_RECOMMEND_SYSTEM = (
    "당신은 SNS 계정 컨설턴트다. 채널 프로필과 트렌드 다이제스트를 보고, 이 계정이 "
    "지금 잡으면 좋을 방향을 추천한다. 반드시 JSON 객체 하나만 출력한다: "
    '{"direction": "콘텐츠 방향 2~3문장", "focus_subs": ["우선 세부주제 최대 3개"], '
    '"hot_trends": ["근거가 된 트렌드 항목 — 다이제스트의 문구를 그대로 복사, 최대 5개"], '
    '"name_ideas": ["주제·톤에 어울리는 채널 이름 후보 3개 — 짧고 기억하기 쉽게"], '
    '"tune_ideas": ["운영자가 추가로 조정해볼 만한 포인트 3개 — '
    "'~해줘' 꼴의 한 줄 요청문\"]}. "
    "다이제스트에 없는 트렌드를 지어내지 않는다."
)


def recommend(
    model: BaseChatModel, profile: ChannelProfile, digest: TrendDigest
) -> dict[str, object] | None:
    """LLM 1회 호출로 추천안 생성. 검증 실패·LLM 장애는 None(추천 없이 진행)."""
    known_items = {item for result in digest.source_results if result.ok for item in result.items}
    prompt = (
        f"채널 프로필:\n{json.dumps(profile_to_json(profile), ensure_ascii=False)}\n\n"
        f"트렌드 다이제스트:\n{digest.digest_markdown}"
    )
    try:
        resp = model.invoke(
            [SystemMessage(content=_RECOMMEND_SYSTEM), HumanMessage(content=prompt)]
        )
        raw = _extract_json(_message_text(resp.content))
    except Exception:
        return None  # LLM 장애 — 추천 없이 진행

    direction = raw.get("direction")
    if not isinstance(direction, str) or not direction.strip():
        return None
    focus = _str_list(raw.get("focus_subs"))[:MAX_FOCUS_SUBS]
    # 근거 규율: 다이제스트에 실제로 있는 항목만 남긴다(topic.py와 동형).
    trends = [t for t in _str_list(raw.get("hot_trends")) if t in known_items][:MAX_HOT_TRENDS]
    names = [n for n in _str_list(raw.get("name_ideas")) if len(n) <= MAX_NAME_CHARS]
    tunes = [t for t in _str_list(raw.get("tune_ideas")) if len(t) <= MAX_TUNE_CHARS]
    return {
        "direction": direction.strip(),
        "focus_subs": focus,
        "hot_trends": trends,
        "name_ideas": names[:MAX_NAME_IDEAS],
        "tune_ideas": tunes[:MAX_TUNE_IDEAS],
    }


def make_recommend_fn(
    model: BaseChatModel, trend_provider: TrendProvider = default_trend_provider
) -> Callable[[ChannelProfile], dict[str, object] | None]:
    """웹 앱 주입용 조립 — 트렌드 조회 + 추천을 한 호출로."""

    def fn(profile: ChannelProfile) -> dict[str, object] | None:
        return recommend(model, profile, trend_provider(profile))

    return fn


_REFINE_SYSTEM = (
    "당신은 SNS 계정 프로필 편집자다. 현재 프로필 JSON과 운영자의 요청 한 줄을 받아, "
    "요청을 반영한 **전체 프로필 JSON**을 출력한다. 형식·키는 입력과 동일하게 유지하고, "
    "요청과 무관한 값은 바꾸지 않는다. tone은 professional/casual/humor/story 중 하나, "
    "goal_ref는 reach_growth/follower_growth/engagement_depth/watch_through 중 하나, "
    "topic_subs는 최대 3개다. JSON 객체 하나만 출력한다."
)


def refine(model: BaseChatModel, profile: ChannelProfile, note: str) -> ChannelProfile:
    """줄글 1회 반영. LLM 산출은 parse_profile 검증을 통과해야 하며, 캐릭터
    이미지·추천안 원문은 원본으로 되덮는다. 실패는 예외 — 호출자(웹 앱)가
    줄글 원문 보존으로 폴백한다."""
    prompt = (
        f"현재 프로필:\n{json.dumps(profile_to_json(profile), ensure_ascii=False)}\n\n"
        f"운영자 요청: {note}"
    )
    resp = model.invoke([SystemMessage(content=_REFINE_SYSTEM), HumanMessage(content=prompt)])
    raw = dict(_extract_json(_message_text(resp.content)))

    # 줄글이 건드릴 수 없는 필드: 캐릭터 이미지(유료 재생성 차단)·추천안(관측 기록).
    character = raw.get("character")
    style = character.get("style") if isinstance(character, Mapping) else None
    raw["character"] = {
        "style": style if isinstance(style, str) else profile.character_style,
        "image_url": profile.character_image_url,
        "checksum": profile.character_checksum,
    }
    raw["recommendation"] = None if profile.recommendation is None else dict(profile.recommendation)
    raw["note"] = note
    return parse_profile(raw)


def make_refine_fn(
    model: BaseChatModel,
) -> Callable[[ChannelProfile, str], ChannelProfile]:
    def fn(profile: ChannelProfile, note: str) -> ChannelProfile:
        return refine(model, profile, note)

    return fn


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part if isinstance(part, str) else str(part.get("text", ""))
            for part in content
            if isinstance(part, (str, dict))
        )
    return str(content)


def _extract_json(text: str) -> Mapping[str, object]:
    """LLM 출력에서 JSON 객체 하나를 꺼낸다(코드펜스·앞뒤 잡담 허용)."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ProfileError(f"JSON 객체를 찾지 못함: {text[:120]!r}")
    try:
        data = json.loads(cleaned[start : end + 1])
    except ValueError as e:
        raise ProfileError(f"JSON 파싱 실패: {e}") from e
    if not isinstance(data, dict):
        raise ProfileError("JSON 최상위가 객체가 아님")
    return data


def _str_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [item.strip() for item in raw if isinstance(item, str) and item.strip()]
