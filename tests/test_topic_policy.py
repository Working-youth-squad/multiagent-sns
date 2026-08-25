"""topic_major 파생 정책 — 팩을 대신하는 분기의 계약.

팩은 닫힌 등록제라 인터뷰의 "직접 입력" 주제를 못 받았다(`resolve_domain`이 거부한다).
파생 함수는 else 분기로 모르는 주제를 받는다. 그 성질과, 파생값이 가리키는 이름이
실재하는지를 여기서 강제한다.

**유효성 검증은 여기 없다.** 빈 문자열·공백은 [sns.onboarding.profile.parse_profile]이
`ProfileError`로 막는다. 여기서 다시 검사하면 정본 파서와 진실이 둘이 된다.
"""

import pytest

from sns.render.concept_image import CONCEPT_FIELDS, ConceptError, parse_concept
from sns.render.video.spec import VideoSpecError, parse_video_spec
from sns.topic_policy import (
    DEV_MAJOR,
    categories_for,
    concept_examples_for,
    concept_kinds_for,
    square_guidance_for,
    square_sources_for,
    subject_label_for,
)

OFFERED = (DEV_MAJOR, "요리", "음악", "춤")  # 인터뷰가 제시하는 대분류
UNKNOWN_BUT_VALID = ("뜨개질", "Gardening")  # "직접 입력"으로 들어올 수 있는 값
ALL_VALID = OFFERED + UNKNOWN_BUT_VALID


@pytest.mark.parametrize("major", ALL_VALID)
def test_concept_kinds_exist_in_the_renderer(major: str) -> None:
    """없는 개념 그림 종류를 적으면 렌더 시점에야 터진다 — 여기서 잡는다."""
    unknown = set(concept_kinds_for(major)) - set(CONCEPT_FIELDS)
    assert not unknown, f"{major}: 렌더러가 모르는 개념 그림 {sorted(unknown)}"


@pytest.mark.parametrize("major", ALL_VALID)
def test_every_concept_kind_has_a_prompt_example(major: str) -> None:
    examples = concept_examples_for(major)
    missing = [k for k in concept_kinds_for(major) if not examples.get(k)]
    assert not missing, f"{major}: 예시 없는 개념 그림 {missing}"


@pytest.mark.parametrize("major", UNKNOWN_BUT_VALID)
def test_unknown_but_valid_major_gets_generic_policy(major: str) -> None:
    """팩이 못 하던 것 — 직접 입력 주제도 사이클이 돌아야 한다."""
    assert categories_for(major) == categories_for("요리")
    assert concept_kinds_for(major) == concept_kinds_for("요리")
    assert square_sources_for(major) == square_sources_for("요리")
    assert square_guidance_for(major) == square_guidance_for("요리")


def test_non_dev_major_has_no_code_source() -> None:
    """요리 채널 spec에 code가 들어오면 파서가 거부해야 한다 — 그 근거가 여기다."""
    assert "code" in square_sources_for(DEV_MAJOR)
    for major in ("요리", "음악", "춤", "뜨개질"):
        assert "code" not in square_sources_for(major), major


def test_non_dev_major_has_no_terminal_concept() -> None:
    """terminal은 설치 명령 그림이라 개발 전용이다."""
    assert "terminal" in concept_kinds_for(DEV_MAJOR)
    assert "terminal" not in concept_kinds_for("요리")


def test_dev_guidance_mentions_code_and_generic_does_not() -> None:
    assert "code" in square_guidance_for(DEV_MAJOR)
    assert "code" not in square_guidance_for("요리")


def test_subject_label_keeps_the_major_for_unknown() -> None:
    assert subject_label_for(DEV_MAJOR) == "개발자"
    assert subject_label_for("요리") == "요리"
    assert subject_label_for("뜨개질") == "뜨개질"


def test_dev_categories_are_unchanged() -> None:
    """이사가 개발 도메인 동작을 바꾸지 않았다는 증거."""
    assert categories_for(DEV_MAJOR) == ("신기술", "기초지식", "꿀팁", "현직자일상", "개발자유머")


def test_examples_are_read_only() -> None:
    """code-owned configuration이다 — 호출자가 바꾸면 이후 모든 프롬프트가 오염된다."""
    examples = concept_examples_for(DEV_MAJOR)
    with pytest.raises(TypeError):
        examples["emphasis"] = "오염"  # type: ignore[index]


# ── 정사각 소스가 파서에 실제로 걸리는가 (옛 tests/test_domain.py에서 이관) ──────
# 프롬프트에서 안내를 뺀 것만으로는 파서가 여전히 받아준다 — 에이전트가 실수로 넣으면
# 요리 영상에 파이썬 코드가 렌더된다.

_MINIMAL: dict[str, object] = {
    "topic": "분갈이 이렇게 하세요",
    "slides": [{"subtitle": "언제", "narration": "뿌리가 화분을 꽉 채우면 때가 된 겁니다."}],
}
_SLIDE: dict[str, object] = dict(_MINIMAL["slides"][0])  # type: ignore[index,arg-type]


def test_parse_rejects_square_field_the_major_does_not_use() -> None:
    """코드를 안 쓰는 주제에 code가 들어오면 렌더까지 가기 전에 끊는다."""
    spec = {**_MINIMAL, "slides": [{**_SLIDE, "code": "print(1)"}]}
    with pytest.raises(VideoSpecError, match="code"):
        parse_video_spec(spec, topic_major="요리")


def test_parse_still_accepts_square_fields_the_major_uses() -> None:
    slide = {**_SLIDE, "image_query": "potted plant"}
    parsed = parse_video_spec({**_MINIMAL, "slides": [slide]}, topic_major="요리")
    assert parsed.slides[0].image_query == "potted plant"


def test_dev_major_accepts_code_field() -> None:
    slide = {**_SLIDE, "code": "print(1)"}
    parsed = parse_video_spec({**_MINIMAL, "slides": [slide]}, topic_major=DEV_MAJOR)
    assert parsed.slides[0].code == "print(1)"


def test_video_spec_carries_the_major_square_order() -> None:
    """렌더러는 주제를 모른다 — 순서를 spec에 실어 보내야 결정론이 유지된다."""
    parsed = parse_video_spec(_MINIMAL, topic_major="요리")
    assert parsed.square_sources == square_sources_for("요리")


def test_generated_image_rule_is_skipped_without_code() -> None:
    """'코드 영상엔 생성 이미지 금지'는 코드를 쓰는 주제에서만 의미가 있다."""
    slide = {**_SLIDE, "image_prompt": "a small green plant on a desk"}
    parsed = parse_video_spec({**_MINIMAL, "slides": [slide]}, topic_major="요리")
    assert parsed.slides[0].image_prompt


def test_parse_concept_rejects_kind_outside_the_major() -> None:
    raw = {"kind": "terminal", "commands": ["pip install foo"], "note": "설명"}
    with pytest.raises(ConceptError):
        parse_concept(raw, kinds=concept_kinds_for("요리"))


def test_topic_major_is_required() -> None:
    """silent default가 있으면 새 호출부가 요리 채널에 개발 규칙을 조용히 적용한다."""
    with pytest.raises(TypeError):
        parse_video_spec(_MINIMAL)  # type: ignore[call-arg]
