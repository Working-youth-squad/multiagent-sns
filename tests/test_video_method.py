"""VideoMethod 생애주기 검증 — 해소 전후로 필수 필드가 다르다.

`parse_video_spec`은 두 시점에 불린다: `set_media_spec`(해소 전)과 렌더 직전(해소 후).
평평한 허용 집합으로는 "해소 전엔 scene_prompt, 해소 후엔 scene_ref"를 표현할 수 없다.
"""

import pytest

from sns.render.video.spec import VideoSpecError, parse_video_spec
from sns.topic_policy import DEV_MAJOR

_BASE: dict[str, object] = {"topic": "테스트 주제"}


def _spec(method: str, slide: dict[str, object]) -> dict[str, object]:
    return {
        **_BASE,
        "method": method,
        "slides": [{"subtitle": "부제", "narration": "한 줄.", **slide}],
    }


def test_plan_stage_requires_the_prompt() -> None:
    with pytest.raises(VideoSpecError, match="scene_prompt"):
        parse_video_spec(_spec("generated_scene", {}), topic_major="요리", stage="plan")


def test_plan_stage_rejects_a_resolved_ref() -> None:
    """LLM이 저장소 URL을 환각으로 써넣는 경로를 막는다."""
    slide = {"scene_prompt": "a warm kitchen", "scene_ref": "mem://image/fake.png"}
    with pytest.raises(VideoSpecError, match="scene_ref"):
        parse_video_spec(_spec("generated_scene", slide), topic_major="요리", stage="plan")


def test_render_stage_requires_the_ref() -> None:
    slide = {"scene_prompt": "a warm kitchen"}
    with pytest.raises(VideoSpecError, match="scene_ref"):
        parse_video_spec(_spec("generated_scene", slide), topic_major="요리", stage="render")


def test_render_stage_accepts_a_recorded_failure() -> None:
    """non-retryable 실패가 기록된 컷엔 scene_ref가 영원히 없다 — 폴백 티켓이다."""
    slide = {"scene_prompt": "a warm kitchen", "scene_failure": {"kind": "safety"}}
    spec = parse_video_spec(_spec("generated_scene", slide), topic_major="요리", stage="render")
    assert spec.slides[0].scene_ref == ""


def test_method_field_lock() -> None:
    """선언한 method가 안 쓰는 필드는 거부한다."""
    slide = {"scene_prompt": "x", "code": "print(1)"}
    with pytest.raises(VideoSpecError, match="code"):
        parse_video_spec(_spec("generated_scene", slide), topic_major=DEV_MAJOR, stage="plan")


def test_slide_method_defaults_to_spec_method() -> None:
    slide = {"scene_prompt": "x", "scene_ref": "mem://image/a.png"}
    spec = parse_video_spec(_spec("generated_scene", slide), topic_major="요리")
    assert spec.method == "generated_scene"
    assert spec.slides[0].method == "generated_scene"


def test_template_is_the_default_method() -> None:
    """기존 media_spec은 method가 없다 — 동작이 안 바뀌어야 한다."""
    spec = parse_video_spec(
        {**_BASE, "slides": [{"subtitle": "부제", "narration": "한 줄."}]}, topic_major=DEV_MAJOR
    )
    assert spec.method == "template"
    assert spec.slides[0].method == "template"


def test_unknown_method_is_refused() -> None:
    with pytest.raises(VideoSpecError, match="method"):
        parse_video_spec(_spec("generated_clip_v2", {}), topic_major="요리")


def test_template_still_takes_its_own_fields() -> None:
    """기존 트랙이 안 깨졌다는 증거."""
    slide = {"code": "print(1)", "lang": "python"}
    spec = parse_video_spec(_spec("template", slide), topic_major=DEV_MAJOR)
    assert spec.slides[0].code == "print(1)"
