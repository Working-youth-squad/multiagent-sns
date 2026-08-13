"""C3 품질 게이트 검증 — FR-Q1/Q2/Q4·FR-A2 카드 항목. 순수 함수 결정론."""

from sns.quality.gate import (
    MAX_CONTENT_SIMILARITY,
    QualityReport,
    check_card,
    content_signature,
    contrast_ratio,
)
from sns.render.card import parse_card_spec, render_card

VALID_SPEC: dict[str, object] = {
    "hook": "You are shipping bugs in your sleep",
    "title": "3 Postgres indexes every backend dev misses",
    "body": ["Partial indexes cut write cost.", "BRIN wins on time-series."],
    "footer": "Save this for later",
}


def _report(spec_overrides: dict[str, object] | None = None, **kwargs: object) -> QualityReport:
    spec = parse_card_spec({**VALID_SPEC, **(spec_overrides or {})})
    render = render_card(spec)
    return check_card(spec, render, **kwargs)  # type: ignore[arg-type]


def test_clean_card_passes() -> None:
    report = _report()
    assert report.status == "passed"
    assert report.passed
    assert all(c.passed for c in report.checks)


def test_low_contrast_fails() -> None:
    # 전경/배경이 거의 같은 색 → 대비 하한 미달.
    report = _report({"palette": {"background": "#202020", "foreground": "#282828"}})
    assert report.status == "failed"
    failed = [c.name for c in report.checks if not c.passed]
    assert failed == ["min_contrast"]


def test_overflow_fails() -> None:
    report = _report({"body": [f"crowded line {i}" for i in range(40)]})
    assert report.status == "failed"
    assert any(c.name == "text_overflow" and not c.passed for c in report.checks)


def test_low_resolution_fails() -> None:
    report = _report({"width": 500, "height": 500})
    assert report.status == "failed"
    assert any(c.name == "min_resolution" and not c.passed for c in report.checks)


def test_near_duplicate_content_fails() -> None:
    # 직전 게시물과 동일 콘텐츠 → 유사도 상한 초과(FR-A2).
    prior = content_signature(parse_card_spec(VALID_SPEC))
    report = _report(recent_signatures=(prior,))
    assert report.status == "failed"
    assert any(c.name == "content_similarity" and not c.passed for c in report.checks)


def test_distinct_content_passes_similarity() -> None:
    other = content_signature(
        parse_card_spec(
            {
                "hook": "Totally unrelated hook about frontend",
                "title": "React server components explained",
                "body": ["Streaming HTML changes everything."],
                "footer": "Follow for more",
            }
        )
    )
    report = _report(recent_signatures=(other,))
    assert report.status == "passed"


def test_contrast_ratio_symmetric_and_bounded() -> None:
    assert contrast_ratio("#000000", "#ffffff") == contrast_ratio("#ffffff", "#000000")
    assert round(contrast_ratio("#000000", "#ffffff"), 1) == 21.0
    assert round(contrast_ratio("#123456", "#123456"), 1) == 1.0


def test_report_serializes_for_jsonb() -> None:
    payload = _report().to_json()
    assert payload["status"] == "passed"
    assert isinstance(payload["checks"], list)
    first = payload["checks"][0]
    assert set(first) == {"name", "passed", "detail"}


def test_gate_never_emits_needs_review() -> None:
    # 자동 게이트는 passed/failed만 — needs_review는 hybrid 사람 관문(FR-Q3).
    assert _report().status in ("passed", "failed")
    assert MAX_CONTENT_SIMILARITY == 0.8  # 임계값 상수 고정 확인
