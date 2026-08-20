"""균형 줄바꿈 — 어절이 자연스러운 자리에서 갈리게 한다.

폭이 찰 때까지 밀어넣는 그리디 방식은 마지막 줄에만 짧게 남겨 어절 덩어리를 깬다
("내가 내일 트럭에 / 치이면?"). 줄 수는 그대로 두고 줄 간 폭 편차만 최소화하면
의미 단위로 갈린다("내가 내일 / 트럭에 치이면?"). CSS `text-wrap: balance`와 같은 개념.
"""

from sns.render.text import display_width, wrap_balanced


def w(text: str) -> int:
    """폭 측정기 — 실제 렌더러는 폰트 메트릭을 넘긴다."""
    return display_width(text)


def test_keeps_phrase_together() -> None:
    assert wrap_balanced("내가 내일 트럭에 치이면?", w, 16) == ["내가 내일", "트럭에 치이면?"]


def test_keeps_parenthetical_together() -> None:
    assert wrap_balanced("트럭 지수 (Truck Factor)", w, 16) == ["트럭 지수", "(Truck Factor)"]


def test_single_line_when_it_fits() -> None:
    assert wrap_balanced("짧은 제목", w, 40) == ["짧은 제목"]


def test_line_count_matches_greedy() -> None:
    """균형을 잡자고 줄이 늘어나면 레이아웃이 깨진다 — 최소 줄 수를 유지한다."""
    text = "메인 서비스를 돕는 든든한 조력자 사이드카 패턴"
    for limit in (12, 16, 20, 24, 30):
        lines = wrap_balanced(text, w, limit)
        assert all(w(line) <= limit for line in lines), (limit, lines)
        # 그리디가 만드는 줄 수 = 가능한 최소 줄 수
        greedy, cur = 1, ""
        for tok in text.split(" "):
            trial = tok if not cur else f"{cur} {tok}"
            if w(trial) <= limit:
                cur = trial
            else:
                greedy += 1
                cur = tok
        assert len(lines) == greedy, (limit, lines)


def test_explicit_newline_forces_break() -> None:
    """에이전트가 의도적으로 끊은 자리는 존중한다."""
    assert wrap_balanced("앞줄\n뒷줄", w, 40) == ["앞줄", "뒷줄"]


def test_long_token_split_by_character() -> None:
    """공백 없는 긴 토큰(한글 문장 등)은 글자 단위로 쪼갠다 — 넘치게 두지 않는다."""
    lines = wrap_balanced("가나다라마바사아자차카타", w, 8)
    assert all(w(line) <= 8 for line in lines)
    assert "".join(lines) == "가나다라마바사아자차카타"


def test_empty_text_yields_single_empty_line() -> None:
    assert wrap_balanced("", w, 10) == [""]
