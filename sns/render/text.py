"""렌더 공용 텍스트 유틸 — 카드·영상이 함께 쓰는 폭 계산·문장 분절·줄바꿈.

`display_width`/`split_sentences`는 "LLM 산출 텍스트가 화면/시간에 실제로 들어가는가"를
렌더 전에 판정하는 데 쓴다 — 기준은 글자수가 아니라 **표시 폭**이다(한글 글리프 자간이
라틴의 약 2배). 폰트가 없어도 계산되므로 스펙 파싱 단계에서 쓸 수 있다.

`wrap_balanced`는 렌더 시점의 줄바꿈이라 실제 폰트 메트릭이 필요하다. 그래서 폭 측정을
**주입**받는다 — 덕분에 Pillow 없이도 단위 테스트가 된다.
"""

import re
from collections.abc import Callable
from unicodedata import east_asian_width

# 종결부호 뒤 공백에서 끊는다. 종결부호가 없으면 통째로 한 문장.
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")


def display_width(text: str) -> int:
    """표시 폭 — 전각(한글·CJK)은 2, 그 외는 1. 렌더 폭의 폰트 무관 근사치."""
    return sum(2 if east_asian_width(c) in "WF" else 1 for c in text)


def split_sentences(text: str) -> tuple[str, ...]:
    """나레이션을 문장 단위로 쪼갠다 — 영상의 **컷 경계**가 된다.

    한 문장 = 한 컷이라, 나레이션이 길어도 화면이 문장마다 바뀐다(FR-A2 2~4초 전환).
    빈/공백 문자열은 빈 튜플. 종결부호가 없으면 전체가 한 문장.
    """
    stripped = text.strip()
    if not stripped:
        return ()
    return tuple(part.strip() for part in _SENTENCE_END.split(stripped) if part.strip())


# 폭 측정기 — 렌더러는 폰트 메트릭을, 테스트는 display_width를 넘긴다.
Measure = Callable[[str], float]


def _split_long_token(token: str, measure: Measure, max_width: float) -> list[str]:
    """공백 없이 폭을 넘는 토큰을 글자 단위로 쪼갠다(한글 대응). 넘치게 두지 않는다."""
    out: list[str] = []
    buf = ""
    for ch in token:
        if buf and measure(buf + ch) > max_width:
            out.append(buf)
            buf = ch
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def _tokens(paragraph: str, measure: Measure, max_width: float) -> list[str]:
    out: list[str] = []
    for token in paragraph.split(" "):
        if not token:
            continue
        if measure(token) <= max_width:
            out.append(token)
        else:
            out.extend(_split_long_token(token, measure, max_width))
    return out


def _min_lines(tokens: list[str], measure: Measure, max_width: float) -> int:
    """그리디가 만드는 줄 수 = 가능한 최소 줄 수."""
    count, current = 1, ""
    for token in tokens:
        trial = token if not current else f"{current} {token}"
        if measure(trial) <= max_width:
            current = trial
        else:
            count += 1
            current = token
    return count


def _balance(tokens: list[str], measure: Measure, max_width: float, lines: int) -> list[str]:
    """토큰을 `lines`줄로 나누되 **가장 긴 줄을 최소화**한다(줄 간 편차 축소).

    DP: best[i][k] = 토큰 i부터 k줄로 나눌 때의 최소 '최대 줄 폭'. 완전탐색은 토큰이
    늘면 조합이 폭발하지만 이건 O(n²·k)라 긴 단락에도 안전하다.
    """
    n = len(tokens)
    # widths[i][j] = 토큰 i..j-1을 한 줄로 합쳤을 때의 폭 (넘치면 무한대)
    widths = [[float("inf")] * (n + 1) for _ in range(n + 1)]
    for i in range(n):
        for j in range(i + 1, n + 1):
            width = measure(" ".join(tokens[i:j]))
            if width > max_width:
                break
            widths[i][j] = width

    best = [[float("inf")] * (lines + 1) for _ in range(n + 1)]
    cut = [[0] * (lines + 1) for _ in range(n + 1)]
    best[n][0] = 0.0
    for i in range(n - 1, -1, -1):
        for k in range(1, lines + 1):
            for j in range(i + 1, n + 1):
                if widths[i][j] == float("inf"):
                    break
                cost = max(widths[i][j], best[j][k - 1])
                if cost < best[i][k]:
                    best[i][k], cut[i][k] = cost, j
    if best[0][lines] == float("inf"):  # 이론상 도달 불가 — 방어적 폴백
        return [" ".join(tokens)]

    out: list[str] = []
    i, k = 0, lines
    while k:
        j = cut[i][k]
        out.append(" ".join(tokens[i:j]))
        i, k = j, k - 1
    return out


def wrap_balanced(text: str, measure: Measure, max_width: float) -> list[str]:
    """줄 수는 그리디와 같게 유지하면서 줄 간 폭 편차를 줄인 줄바꿈.

    그리디는 앞줄을 꽉 채우고 마지막 줄에만 짧게 남겨 어절 덩어리를 깬다
    ("내가 내일 트럭에 / 치이면?"). 균형을 잡으면 의미 단위에서 갈린다
    ("내가 내일 / 트럭에 치이면?"). 명시적 `\n`은 강제 개행으로 존중한다.
    """
    result: list[str] = []
    for paragraph in text.split("\n"):
        tokens = _tokens(paragraph, measure, max_width)
        if not tokens:
            result.append("")
            continue
        lines = _min_lines(tokens, measure, max_width)
        result.extend(
            [" ".join(tokens)] if lines == 1 else _balance(tokens, measure, max_width, lines)
        )
    return result
