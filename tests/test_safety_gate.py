"""FR-Q7 텍스트 안전 검열 — 결정론, 네트워크 0, LLM 0.

FR-Q7은 금지어/카테고리를 **코드 상수로 외부화**하고 같은 입력이 같은 판정을 내도록
요구한다. 이미지 게이트([sns.render.images.gate])와 같은 규율의 텍스트판이다.

**이 게이트가 막는 것은 "계정이 날아가는 소재"뿐이다.** 과장은 쇼츠 후크의 핵심이라
막지 않는다("확 빨라집니다"는 정상). 근거 없는 구체 수치("300%")도 여기서 막지 않는다 —
정당한 수치("메모리 40% 감소")까지 죽어서 오탐 비용이 크다. 그건 Content Agent
프롬프트가 다룬다.

사실 오류("Prettier가 타입 오류를 잡아준다")는 룰로 못 잡는다. FR-Q7의 LLM 이중 검사
몫이고, 비결정론이라 별도 조각이다.
"""

import pytest

from sns.quality.safety import (
    BLOCKED_TERMS,
    screen_content,
    screen_text,
)

SPEC: dict[str, object] = {
    "topic": "리스트에서 in 쓰지 마세요",
    "slides": [
        {"subtitle": "왜 느린가", "narration": "in 연산자는 처음부터 끝까지 훑습니다."},
    ],
}


# ── 통과해야 하는 것 (오탐 방어) ──────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "정당한 이유가 있으면 예외를 던지세요.",  # '정당'
        "대선배 개발자에게 배운 팁입니다.",  # '대선'
        "API를 외부에 노출하지 마세요.",  # '노출'
        "프록시로 방화벽을 우회합니다.",  # '우회'
        "이 설정만 바꿔도 생산성이 300% 올라갑니다.",  # 과장 수치는 게이트 밖
        "이거 모르면 진짜 손해입니다.",  # 후크 과장
        "최악의 경우 십만 번 비교합니다.",  # '최악'
    ],
)
def test_normal_dev_text_passes(text: str) -> None:
    """한국어는 띄어쓰기가 단어 경계가 아니다 — 짧은 금지어는 멀쩡한 문장을 죽인다."""
    assert screen_text(text, where="본문") == ()


def test_clean_content_passes() -> None:
    assert screen_content(body="파이썬 팁입니다. #개발", media_spec=SPEC) == ()


# ── 막아야 하는 것 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("음란물 사이트 만드는 법", "nsfw"),
        ("대통령이 어제 발표한 정책", "political"),
        ("국회의원 발언을 정리했습니다", "political"),
        ("이 개발자는 사기꾼입니다", "abuse"),
        ("정품 대신 크랙 버전을 받으세요", "piracy"),
        ("키젠으로 라이선스를 만듭니다", "piracy"),
    ],
)
def test_blocked_material_is_caught(text: str, category: str) -> None:
    findings = screen_text(text, where="본문")
    assert findings, f"{text!r}가 통과함"
    assert findings[0].category == category


def test_finding_names_where_it_came_from() -> None:
    """어디를 고쳐야 하는지 모르면 같은 원고가 계속 돌아온다."""
    [finding] = screen_text("대통령 연설 요약", where="slides[2].narration")
    assert finding.where == "slides[2].narration"
    assert finding.term in "대통령 연설 요약"


def test_case_insensitive_for_latin() -> None:
    assert screen_text("KEYGEN 사용법", where="본문")


def test_screening_covers_every_text_in_the_spec() -> None:
    """대본은 통과했는데 자막이 안 걸리면 게이트가 뚫린 것이다."""
    spec = {
        "topic": "대통령 연설 분석",
        "slides": [{"subtitle": "부제", "narration": "한 문장."}],
    }
    assert any(f.where == "topic" for f in screen_content(body="본문", media_spec=spec))

    spec2 = {
        "topic": "정상 주제",
        "slides": [{"subtitle": "크랙 받는 법", "narration": "한 문장."}],
    }
    assert any("subtitle" in f.where for f in screen_content(body="본문", media_spec=spec2))

    spec3 = {
        "topic": "정상 주제",
        "slides": [{"subtitle": "부제", "narration": "음란물 이야기입니다."}],
    }
    assert any("narration" in f.where for f in screen_content(body="본문", media_spec=spec3))


def test_body_is_screened_too() -> None:
    findings = screen_content(body="크랙 다운로드 링크입니다", media_spec=SPEC)
    assert findings and findings[0].where == "body"


# ── 결정론·상수 정합성 ────────────────────────────────────────────


def test_deterministic() -> None:
    text = "대통령 연설과 크랙 이야기"
    assert screen_text(text, where="본문") == screen_text(text, where="본문")


def test_every_blocked_term_actually_blocks() -> None:
    """목록만 늘고 판정이 안 보는 사고를 막는다."""
    for category, terms in BLOCKED_TERMS.items():
        for term in terms:
            findings = screen_text(f"{term} 관련 이야기입니다", where="본문")
            assert findings, f"{category}/{term}이 통과함"


def test_all_findings_reported_not_just_the_first() -> None:
    """하나 고치고 다시 돌렸더니 또 걸리는 일이 없게 한 번에 다 돌려준다."""
    findings = screen_content(body="대통령 이야기", media_spec={**SPEC, "topic": "크랙 받는 법"})
    assert {f.category for f in findings} == {"political", "piracy"}


def test_malformed_spec_does_not_crash() -> None:
    """게이트가 죽으면 검열 없이 통과하는 것과 같다 — 조용히 넘어가지 않는다."""
    assert screen_content(body="본문", media_spec={"slides": "nope"}) == ()
    assert screen_content(body="본문", media_spec={}) == ()
