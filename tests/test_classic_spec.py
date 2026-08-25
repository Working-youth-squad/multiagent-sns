"""VideoSpec 파싱 방어선 — malformed media_spec은 렌더 진입 전 차단."""

import pytest

from sns.render.text import split_sentences
from sns.render.video.classic.spec import (
    DEFAULT_VOICE,
    MAX_NARRATION_WIDTH,
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


# ── 컷 = 문장 (FR-A2 2~4초 화면 전환) ───────────────────────────────
# 나레이션 길이가 곧 화면 정지 시간이던 구조를 문장 단위로 쪼갠다.
# 실측 발화 속도 8.0자/초 → 한글 31자(폭 62)면 약 3.9초.


def test_split_sentences_on_terminators() -> None:
    assert split_sentences("첫 문장입니다. 둘째 문장이죠! 셋째는요?") == (
        "첫 문장입니다.",
        "둘째 문장이죠!",
        "셋째는요?",
    )


def test_split_sentences_without_terminator_is_single() -> None:
    assert split_sentences("종결부호 없는 한 문장") == ("종결부호 없는 한 문장",)


def test_slide_with_two_sentences_yields_two_cuts() -> None:
    spec = parse_video_spec(
        {"slides": [{"title": "제목", "narration": "앞 문장입니다. 뒤 문장입니다."}]}
    )
    assert len(spec.slides) == 1
    assert len(spec.cuts) == 2
    assert [c.text for c in spec.cuts] == ["앞 문장입니다.", "뒤 문장입니다."]


def test_cut_carries_owning_slide_visuals() -> None:
    spec = parse_video_spec(
        {"slides": [{"title": "화면 제목", "body": "부제", "narration": "한 문장. 두 문장."}]}
    )
    assert len(spec.cuts) == 2
    assert all(c.slide.title == "화면 제목" and c.slide.body == "부제" for c in spec.cuts)


def test_long_sentence_rejected() -> None:
    """한 문장이 상한을 넘으면 그 컷이 4초를 넘겨 FR-A2를 깬다 — 파싱에서 막는다."""
    long_one = "가" * (MAX_NARRATION_WIDTH // 2 + 1) + "."
    with pytest.raises(VideoSpecError, match="narration"):
        parse_video_spec({"slides": [{"title": "제목", "narration": long_one}]})


def test_long_narration_split_into_short_sentences_accepted() -> None:
    """긴 나레이션이라도 문장마다 상한 이내면 통과 — 정보량을 깎지 않는다."""
    # 종결부호도 폭에 포함되므로 상한에서 덜어낸다.
    sentence = "가" * ((MAX_NARRATION_WIDTH - 2) // 2) + ". "
    spec = parse_video_spec({"slides": [{"title": "제목", "narration": sentence * 4}]})
    assert len(spec.cuts) == 4
