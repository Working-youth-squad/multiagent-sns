"""VideoSpec 파싱 방어선 — malformed media_spec은 렌더 진입 전 차단."""

import pytest

from sns.render.video.spec import (
    DEFAULT_VOICE,
    MAX_SIDE,
    MAX_SLIDES,
    VideoSpecError,
    parse_video_spec,
)


def test_parse_defaults_with_legacy_str_slides() -> None:
    spec = parse_video_spec({"slides": ["안녕하세요", "쇼츠 테스트"]})
    assert (spec.width, spec.height) == (1080, 1920)
    assert [s.title for s in spec.slides] == ["안녕하세요", "쇼츠 테스트"]
    assert spec.slides[0].narration_text == "안녕하세요"  # 문자열 = 제목이자 나레이션
    assert spec.voice == DEFAULT_VOICE
    assert spec.accent == "#58a6ff"


def test_parse_structured_slides() -> None:
    spec = parse_video_spec(
        {
            "slides": [
                {"title": "훅 문장", "body": "부연 설명", "narration": "말로 풀어쓴 나레이션."},
                {"title": "제목만"},
            ]
        }
    )
    first = spec.slides[0]
    assert (first.title, first.body) == ("훅 문장", "부연 설명")
    assert first.narration_text == "말로 풀어쓴 나레이션."
    assert spec.slides[1].narration_text == "제목만"  # narration 비면 title+body


def test_structured_slide_requires_title() -> None:
    with pytest.raises(VideoSpecError):
        parse_video_spec({"slides": [{"body": "제목 없음"}]})
    with pytest.raises(VideoSpecError):
        parse_video_spec({"slides": [42]})


def test_slides_required_and_nonempty() -> None:
    with pytest.raises(VideoSpecError):
        parse_video_spec({})
    with pytest.raises(VideoSpecError):
        parse_video_spec({"slides": []})
    with pytest.raises(VideoSpecError):
        parse_video_spec({"slides": ["ok", "  "]})
    with pytest.raises(VideoSpecError):
        parse_video_spec({"slides": ["ok", 42]})


def test_too_many_slides() -> None:
    with pytest.raises(VideoSpecError):
        parse_video_spec({"slides": ["x"] * (MAX_SLIDES + 1)})


def test_dimension_bomb_guard() -> None:
    with pytest.raises(VideoSpecError):
        parse_video_spec({"slides": ["x"], "width": MAX_SIDE * 100, "height": MAX_SIDE * 100})


def test_aspect_must_be_9_16() -> None:
    with pytest.raises(VideoSpecError):
        parse_video_spec({"slides": ["x"], "width": 1080, "height": 1080})
    spec = parse_video_spec({"slides": ["x"], "width": 720, "height": 1280})
    assert (spec.width, spec.height) == (720, 1280)


def test_invalid_color() -> None:
    with pytest.raises(VideoSpecError):
        parse_video_spec({"slides": ["x"], "background": "blue"})
