"""VideoSpec 파싱 방어선 — malformed media_spec은 렌더 진입 전 차단."""

import pytest

from sns.render.video.spec import (
    DEFAULT_VOICE,
    MAX_SIDE,
    MAX_SLIDES,
    VideoSpecError,
    parse_video_spec,
)


def test_parse_defaults() -> None:
    spec = parse_video_spec({"slides": ["안녕하세요", "쇼츠 테스트"]})
    assert (spec.width, spec.height) == (1080, 1920)
    assert spec.slides == ("안녕하세요", "쇼츠 테스트")
    assert spec.voice == DEFAULT_VOICE


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
