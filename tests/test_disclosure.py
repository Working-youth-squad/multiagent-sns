"""AI 생성 표기 — 코드가 붙인다, LLM이 아니라.

06 §5가 생성형 영상을 스코프 아웃한 사유 (c) "콘텐츠 정책(비진정성) 리스크"에 대한 답이다.
Content 에이전트가 캡션에 써주기를 기대하면 안 쓰는 날이 오고, 그날 표기 없는 합성 영상이
자동 발행된다.
"""

from sns.publish.disclosure import AI_DISCLOSURE, needs_disclosure, with_ai_disclosure


def test_template_needs_no_disclosure() -> None:
    """코드·개념 그림·스톡 사진은 합성 콘텐츠가 아니다. 전부에 붙이면 표기가 의미를 잃는다."""
    assert not needs_disclosure({"method": "template", "slides": []})


def test_absent_method_needs_no_disclosure() -> None:
    """method 없는 기존 media_spec은 템플릿이다."""
    assert not needs_disclosure({"slides": []})


def test_card_spec_needs_no_disclosure() -> None:
    """카드에는 method가 없다 — 슬라이드도 없다."""
    assert not needs_disclosure({"hook": "h", "title": "t", "body": ["x"], "footer": "f"})


def test_generated_scene_needs_disclosure() -> None:
    assert needs_disclosure({"method": "generated_scene", "slides": []})


def test_hybrid_needs_disclosure_when_any_cut_is_generated() -> None:
    spec = {
        "method": "hybrid",
        "slides": [{"method": "template"}, {"method": "generated_scene"}],
    }
    assert needs_disclosure(spec)


def test_hybrid_without_generated_cuts_needs_none() -> None:
    spec = {"method": "hybrid", "slides": [{"method": "template"}]}
    assert not needs_disclosure(spec)


def test_disclosure_is_appended() -> None:
    body = with_ai_disclosure("본문입니다.", {"method": "generated_scene", "slides": []})
    assert body.startswith("본문입니다.")
    assert body.rstrip().endswith(AI_DISCLOSURE)


def test_disclosure_is_idempotent() -> None:
    """사이클 재실행이 표기를 두 번 붙이면 캡션이 지저분해진다."""
    spec = {"method": "generated_scene", "slides": []}
    once = with_ai_disclosure("본문입니다.", spec)
    assert with_ai_disclosure(once, spec) == once


def test_body_is_untouched_for_template() -> None:
    assert with_ai_disclosure("본문입니다.", {"method": "template"}) == "본문입니다."
