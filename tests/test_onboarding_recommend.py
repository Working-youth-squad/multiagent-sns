"""추천·미세조정 — GenericFakeChatModel(네트워크 0)로 검증 규율 확인."""

import json

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from sns.onboarding.profile import ChannelProfile, ProfileError, categories_for
from sns.onboarding.recommend import make_recommend_fn, recommend, refine
from sns.tools.contracts import SourceResult, TrendDigest


def _model(body: str) -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter([AIMessage(content=body)]))


def _profile(**overrides: object) -> ChannelProfile:
    base: dict[str, object] = {
        "topic_major": "개발",
        "topic_subs": ("AI", "파이썬"),
        "tone": "casual",
        "goal_ref": "engagement_depth",
        "character_style": "flat_vector",
        "categories": categories_for("개발"),
    }
    base.update(overrides)
    return ChannelProfile(**base)  # type: ignore[arg-type]


DIGEST = TrendDigest(
    digest_markdown="# 트렌드 다이제스트\n\n## hn\n- GPT-6 공개\n- 파이썬 3.14 출시\n",
    source_results=(SourceResult(source="hn", ok=True, items=("GPT-6 공개", "파이썬 3.14 출시")),),
)


def test_recommend_keeps_only_grounded_trends() -> None:
    out = json.dumps(
        {
            "direction": "AI 최신 소식을 초보 눈높이로 풀자",
            "focus_subs": ["AI"],
            "hot_trends": ["GPT-6 공개", "지어낸 트렌드"],
        },
        ensure_ascii=False,
    )
    rec = recommend(_model(out), _profile(), DIGEST)
    assert rec is not None
    assert rec["hot_trends"] == ["GPT-6 공개"]  # 다이제스트 밖 항목 거부
    assert rec["direction"] == "AI 최신 소식을 초보 눈높이로 풀자"


def test_recommend_parses_name_and_tune_ideas_with_limits() -> None:
    out = json.dumps(
        {
            "direction": "방향",
            "focus_subs": [],
            "hot_trends": [],
            "name_ideas": ["파이썬 한입", "  ", "x" * 31, "코드 스낵", "AI 브리핑", "넘치는 후보"],
            "tune_ideas": ["이모지를 많이 써줘", 42, "y" * 81],
        },
        ensure_ascii=False,
    )
    rec = recommend(_model(out), _profile(), DIGEST)
    assert rec is not None
    # 공백·과다 길이·초과 개수는 걸러지고 최대 3개만 남는다.
    assert rec["name_ideas"] == ["파이썬 한입", "코드 스낵", "AI 브리핑"]
    assert rec["tune_ideas"] == ["이모지를 많이 써줘"]


def test_recommend_none_on_missing_direction_or_bad_json() -> None:
    assert recommend(_model('{"focus_subs": ["AI"]}'), _profile(), DIGEST) is None
    assert recommend(_model("추천드릴게요~"), _profile(), DIGEST) is None


def test_recommend_none_on_model_error() -> None:
    class Boom(GenericFakeChatModel):
        def invoke(self, *a: object, **k: object) -> AIMessage:
            raise RuntimeError("down")

    assert recommend(Boom(messages=iter(())), _profile(), DIGEST) is None


def test_make_recommend_fn_uses_injected_trend_provider() -> None:
    seen: list[str] = []

    def provider(profile: ChannelProfile) -> TrendDigest:
        seen.append(profile.topic_major)
        return DIGEST

    out = json.dumps({"direction": "방향", "focus_subs": [], "hot_trends": []}, ensure_ascii=False)
    fn = make_recommend_fn(_model(out), provider)
    assert fn(_profile()) is not None
    assert seen == ["개발"]  # 탈부착 심으로 주입된 provider가 실제로 쓰인다


def _refined_json(**overrides: object) -> str:
    base: dict[str, object] = {
        "topic_major": "개발",
        "topic_subs": ["AI"],
        "tone": "professional",
        "goal_ref": "engagement_depth",
        "categories": list(categories_for("개발")),
        "character": {"style": "flat_vector", "image_url": "https://evil/x.png"},
    }
    base.update(overrides)
    return "```json\n" + json.dumps(base, ensure_ascii=False) + "\n```"


def test_refine_applies_change_and_locks_character_image() -> None:
    profile = _profile(
        character_image_url="mem://image/orig.png",
        character_checksum="orig",
        recommendation={"direction": "원래 추천"},
    )
    revised = refine(_model(_refined_json()), profile, "전문적인 톤으로, AI만")
    assert revised.tone == "professional"
    assert revised.topic_subs == ("AI",)
    # 줄글이 못 건드리는 필드: 캐릭터 이미지(유료 재생성 차단)·추천 원문·note.
    assert revised.character_image_url == "mem://image/orig.png"
    assert revised.character_checksum == "orig"
    assert revised.recommendation == {"direction": "원래 추천"}
    assert revised.note == "전문적인 톤으로, AI만"


def test_refine_rejects_invalid_llm_output() -> None:
    with pytest.raises(ProfileError):
        refine(_model(_refined_json(goal_ref="go_viral")), _profile(), "목표 바꿔줘")
    with pytest.raises(ProfileError):
        refine(_model("JSON 아님"), _profile(), "아무거나")
