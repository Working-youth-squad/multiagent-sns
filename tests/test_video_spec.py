"""VideoSpec 파싱 방어선 — malformed media_spec은 렌더 진입 전 차단.

3단 레이아웃(주제 / 정사각 / 자막) 기준 모델:
  - `topic`  영상 내내 고정. 시청자가 "무슨 영상인지"를 잃지 않게 하는 앵커.
  - 슬라이드 1장 = 컷 1개 = 화면 1장. 부제·코드·초점·나레이션이 컷마다 바뀐다.

상한값은 전부 실측이다(맑은고딕 기준, 상단 80px·알약 38px·자막 54px).
"""

import pytest

from sns.render.video.spec import (
    DEFAULT_VOICE,
    MAX_NARRATION_WIDTH,
    MAX_SIDE,
    MAX_SLIDES,
    MAX_SUBTITLE_WIDTH,
    MAX_TOPIC_WIDTH,
    VideoSpecError,
    parse_video_spec,
)

MINIMAL: dict[str, object] = {
    "topic": "리스트에서 in 쓰지 마세요",
    "slides": [
        {"subtitle": "왜 느린가", "narration": "in 연산자는 처음부터 끝까지 훑습니다."},
    ],
}


def test_parse_minimal() -> None:
    spec = parse_video_spec(MINIMAL)
    assert spec.topic == "리스트에서 in 쓰지 마세요"
    assert (spec.width, spec.height) == (1080, 1920)
    assert spec.voice == DEFAULT_VOICE
    assert len(spec.slides) == 1
    slide = spec.slides[0]
    assert slide.subtitle == "왜 느린가"
    assert slide.code == "" and slide.focus_lines == ()


def test_slide_carries_code_and_focus() -> None:
    spec = parse_video_spec(
        {
            **MINIMAL,
            "slides": [
                {
                    "subtitle": "해법",
                    "narration": "셋으로 바꾸면 한 번입니다.",
                    "code": "items = set(ids)\nif x in items:\n    go()",
                    "lang": "python",
                    "focus_lines": [1],
                }
            ],
        }
    )
    assert spec.slides[0].lang == "python"
    assert spec.slides[0].focus_lines == (1,)


# ── 주제 (영상 고정 앵커) ──────────────────────────────────────────


def test_topic_required() -> None:
    with pytest.raises(VideoSpecError, match="topic"):
        parse_video_spec({"slides": MINIMAL["slides"]})


def test_topic_too_wide_rejected() -> None:
    """실측: 한글 22자(폭 44)까지 상단에서 2줄. 그 이상은 3줄이 되어 알약을 밀어낸다."""
    with pytest.raises(VideoSpecError, match="topic"):
        parse_video_spec({**MINIMAL, "topic": "가" * (MAX_TOPIC_WIDTH // 2 + 1)})


def test_topic_at_limit_accepted() -> None:
    parse_video_spec({**MINIMAL, "topic": "가" * (MAX_TOPIC_WIDTH // 2)})


# ── 부제 (알약) ───────────────────────────────────────────────────


def test_subtitle_required() -> None:
    with pytest.raises(VideoSpecError, match="subtitle"):
        parse_video_spec({**MINIMAL, "slides": [{"narration": "한 문장입니다."}]})


def test_subtitle_too_wide_rejected() -> None:
    """실측: 한글 20자(폭 40)면 알약이 828px. 그 이상은 화면 폭을 넘는다."""
    with pytest.raises(VideoSpecError, match="subtitle"):
        parse_video_spec(
            {
                **MINIMAL,
                "slides": [
                    {"subtitle": "가" * (MAX_SUBTITLE_WIDTH // 2 + 1), "narration": "한 문장."}
                ],
            }
        )


# ── 나레이션 (TTS + 자막) ─────────────────────────────────────────


def test_narration_required() -> None:
    with pytest.raises(VideoSpecError, match="narration"):
        parse_video_spec({**MINIMAL, "slides": [{"subtitle": "부제"}]})


def test_narration_too_wide_rejected() -> None:
    """한 컷이 곧 한 화면이라 나레이션이 길면 그 화면이 오래 정지한다(FR-A2)."""
    with pytest.raises(VideoSpecError, match="narration"):
        parse_video_spec(
            {
                **MINIMAL,
                "slides": [
                    {"subtitle": "부제", "narration": "가" * (MAX_NARRATION_WIDTH // 2 + 1)}
                ],
            }
        )


# ── 코드 · 초점 ───────────────────────────────────────────────────


def test_code_too_many_lines_rejected() -> None:
    with pytest.raises(VideoSpecError, match="code"):
        parse_video_spec(
            {
                **MINIMAL,
                "slides": [
                    {
                        "subtitle": "부제",
                        "narration": "한 문장.",
                        "code": "\n".join(f"a{i} = {i}" for i in range(40)),
                    }
                ],
            }
        )


def test_focus_out_of_code_range_rejected() -> None:
    with pytest.raises(VideoSpecError, match="focus_lines"):
        parse_video_spec(
            {
                **MINIMAL,
                "slides": [
                    {
                        "subtitle": "부제",
                        "narration": "한 문장.",
                        "code": "x = 1\ny = 2",
                        "focus_lines": [5],
                    }
                ],
            }
        )


def test_focus_without_code_rejected() -> None:
    """초점만 있고 코드가 없으면 가리킬 대상이 없다 — 조용히 무시하지 않는다."""
    with pytest.raises(VideoSpecError, match="focus_lines"):
        parse_video_spec(
            {
                **MINIMAL,
                "slides": [{"subtitle": "부제", "narration": "한 문장.", "focus_lines": [1]}],
            }
        )


# ── 프레임·구조 ───────────────────────────────────────────────────


def test_non_vertical_ratio_rejected() -> None:
    with pytest.raises(VideoSpecError, match="9:16"):
        parse_video_spec({**MINIMAL, "width": 1920, "height": 1080})


def test_dimension_over_max_rejected() -> None:
    with pytest.raises(VideoSpecError, match=str(MAX_SIDE)):
        parse_video_spec({**MINIMAL, "width": MAX_SIDE + 1, "height": (MAX_SIDE + 1) * 16 // 9})


def test_empty_slides_rejected() -> None:
    with pytest.raises(VideoSpecError, match="slides"):
        parse_video_spec({**MINIMAL, "slides": []})


def test_too_many_slides_rejected() -> None:
    one = {"subtitle": "부제", "narration": "한 문장."}
    with pytest.raises(VideoSpecError, match="slides"):
        parse_video_spec({**MINIMAL, "slides": [one] * (MAX_SLIDES + 1)})


def test_custom_palette_parsed() -> None:
    spec = parse_video_spec({**MINIMAL, "accent": "#FF8800"})
    assert spec.accent == "#ff8800"


def test_bad_color_rejected() -> None:
    with pytest.raises(VideoSpecError, match="accent"):
        parse_video_spec({**MINIMAL, "accent": "orange"})


# ── 주제 이미지 ───────────────────────────────────────────────────


def test_image_query_parsed() -> None:
    spec = parse_video_spec(
        {
            **MINIMAL,
            "slides": [
                {"subtitle": "부제", "narration": "한 문장.", "image_query": "network cables"}
            ],
        }
    )
    assert spec.slides[0].image_query == "network cables"
    assert spec.slides[0].image_ref == ""


def test_image_ref_parsed() -> None:
    spec = parse_video_spec(
        {
            **MINIMAL,
            "slides": [
                {"subtitle": "부제", "narration": "한 문장.", "image_ref": "mem://image/ab.png"}
            ],
        }
    )
    assert spec.slides[0].image_ref == "mem://image/ab.png"


def test_image_query_with_code_rejected() -> None:
    """정사각은 하나뿐이다 — 코드와 사진을 같이 주면 무엇을 버릴지 코드가 정하게 된다."""
    with pytest.raises(VideoSpecError, match="image_query"):
        parse_video_spec(
            {
                **MINIMAL,
                "slides": [
                    {
                        "subtitle": "부제",
                        "narration": "한 문장.",
                        "code": "x = 1",
                        "image_query": "network cables",
                    }
                ],
            }
        )


def test_non_ascii_image_query_rejected() -> None:
    """스톡 검색과 금지어 판정이 영어 기준이라, 한글 질의는 게이트를 그냥 지나간다."""
    with pytest.raises(VideoSpecError, match="image_query"):
        parse_video_spec(
            {
                **MINIMAL,
                "slides": [
                    {"subtitle": "부제", "narration": "한 문장.", "image_query": "네트워크"}
                ],
            }
        )


def test_too_long_image_query_rejected() -> None:
    with pytest.raises(VideoSpecError, match="image_query"):
        parse_video_spec(
            {
                **MINIMAL,
                "slides": [
                    {"subtitle": "부제", "narration": "한 문장.", "image_query": "word " * 40}
                ],
            }
        )


# ── 개념 그림 ─────────────────────────────────────────────────────

_CONCEPT: dict[str, object] = {"kind": "emphasis", "headline": "100억"}


def test_concept_parsed() -> None:
    spec = parse_video_spec(
        {**MINIMAL, "slides": [{"subtitle": "부제", "narration": "한 문장.", "concept": _CONCEPT}]}
    )
    concept = spec.slides[0].concept
    assert concept is not None and concept.kind == "emphasis"
    assert concept.fields["headline"] == "100억"


def test_slide_without_concept_is_none() -> None:
    assert parse_video_spec(MINIMAL).slides[0].concept is None


def test_bad_concept_rejected_at_spec_level() -> None:
    """개념 그림의 검증 실패도 렌더 진입 전에 끊긴다 — 코드·이미지와 같은 방어선."""
    with pytest.raises(VideoSpecError, match="concept"):
        parse_video_spec(
            {
                **MINIMAL,
                "slides": [
                    {"subtitle": "부제", "narration": "한 문장.", "concept": {"kind": "pie_chart"}}
                ],
            }
        )


def test_concept_with_code_rejected() -> None:
    with pytest.raises(VideoSpecError, match="concept"):
        parse_video_spec(
            {
                **MINIMAL,
                "slides": [
                    {
                        "subtitle": "부제",
                        "narration": "한 문장.",
                        "code": "x = 1",
                        "concept": _CONCEPT,
                    }
                ],
            }
        )


def test_concept_with_image_query_rejected() -> None:
    with pytest.raises(VideoSpecError, match="concept"):
        parse_video_spec(
            {
                **MINIMAL,
                "slides": [
                    {
                        "subtitle": "부제",
                        "narration": "한 문장.",
                        "image_query": "server room",
                        "concept": _CONCEPT,
                    }
                ],
            }
        )


def test_concept_must_be_a_mapping() -> None:
    with pytest.raises(VideoSpecError, match="concept"):
        parse_video_spec(
            {**MINIMAL, "slides": [{"subtitle": "부제", "narration": "한 문장.", "concept": "x"}]}
        )


def test_image_prompt_parsed() -> None:
    spec = parse_video_spec(
        {
            **MINIMAL,
            "slides": [
                {"subtitle": "부제", "narration": "한 문장.", "image_prompt": "a glowing cube"}
            ],
        }
    )
    assert spec.slides[0].image_prompt == "a glowing cube"


def test_image_prompt_with_code_rejected() -> None:
    with pytest.raises(VideoSpecError, match="image_prompt"):
        parse_video_spec(
            {
                **MINIMAL,
                "slides": [
                    {
                        "subtitle": "부제",
                        "narration": "한 문장.",
                        "code": "x = 1",
                        "image_prompt": "a glowing cube",
                    }
                ],
            }
        )


def test_non_ascii_image_prompt_rejected() -> None:
    with pytest.raises(VideoSpecError, match="image_prompt"):
        parse_video_spec(
            {
                **MINIMAL,
                "slides": [
                    {"subtitle": "부제", "narration": "한 문장.", "image_prompt": "빛나는 정육면체"}
                ],
            }
        )
