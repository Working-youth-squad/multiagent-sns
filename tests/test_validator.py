"""FR-L5 검증기 — 지어낸 수치·없는 post_id·소표본 미표기 거부."""

from sns.learning.validator import validate_analysis
from sns.signals.scoreboard import PostSignals, compute_scoreboard, scoreboard_json, signal_values

_METRICS = {"views": 1000.0, "engaged_views": 800.0, "likes": 50.0}
_TARGET = PostSignals(post_id="XoB6SuTMEvQ", values=signal_values("youtube", _METRICS))
_OTHERS = [
    PostSignals(post_id=f"post{i:07d}", values=signal_values("youtube", _METRICS)) for i in range(5)
]
_SB_OK = scoreboard_json(compute_scoreboard("youtube", _TARGET, _OTHERS, window_index=0))
_SB_EMPTY = scoreboard_json(compute_scoreboard("youtube", _TARGET, [], window_index=0))
_IDS = {"XoB6SuTMEvQ"}

_GOOD_BODY = (
    "이번 게시물의 engaged_rate는 0.8로 기준선 0.8과 같습니다. "
    "다만 동일 품질도 조회수가 10배 차이 날 수 있으므로 단정하지 않습니다."
)


def test_accepts_honest_body() -> None:
    result = validate_analysis(
        _GOOD_BODY, scoreboard_json=_SB_OK, post_ids=_IDS, verdict_available=True
    )
    assert result.ok, result.reasons


def test_rejects_fabricated_number() -> None:
    body = _GOOD_BODY + " 공유율은 37.5%였습니다."
    result = validate_analysis(body, scoreboard_json=_SB_OK, post_ids=_IDS, verdict_available=True)
    assert not result.ok
    assert any("37.5" in r for r in result.reasons)


def test_accepts_percentage_form_of_real_number() -> None:
    body = "engaged_rate는 80%로 평소와 같습니다. 조회수는 10배 차이 날 수 있습니다."
    result = validate_analysis(body, scoreboard_json=_SB_OK, post_ids=_IDS, verdict_available=True)
    assert result.ok, result.reasons


def test_rejects_unknown_post_id() -> None:
    body = _GOOD_BODY + " 특히 dQw4w9WgXcQ 게시물이 흥미롭습니다."
    result = validate_analysis(body, scoreboard_json=_SB_OK, post_ids=_IDS, verdict_available=True)
    assert not result.ok
    assert any("dQw4w9WgXcQ" in r for r in result.reasons)


def test_requires_insufficient_phrase_when_no_verdict() -> None:
    body = "수치가 좋아 보입니다. 조회수는 10배 차이 날 수 있습니다."
    result = validate_analysis(
        body, scoreboard_json=_SB_EMPTY, post_ids=_IDS, verdict_available=False
    )
    assert not result.ok
    ok = validate_analysis(
        "표본이 부족해 판정 불가입니다. 조회수는 10배 차이 날 수 있습니다.",
        scoreboard_json=_SB_EMPTY,
        post_ids=_IDS,
        verdict_available=False,
    )
    assert ok.ok, ok.reasons


def test_requires_variance_warning_always() -> None:
    body = "이번 게시물의 engaged_rate는 0.8로 기준선과 같습니다."
    result = validate_analysis(body, scoreboard_json=_SB_OK, post_ids=_IDS, verdict_available=True)
    assert not result.ok
    assert any("분산 경고" in r for r in result.reasons)
