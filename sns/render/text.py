"""렌더 공용 텍스트 유틸 — 카드·영상 스펙이 함께 쓰는 폭 계산·문장 분절.

둘 다 "LLM 산출 텍스트가 화면/시간에 실제로 들어가는가"를 렌더 전에 판정해야 하고,
그 판정 기준은 글자수가 아니라 **표시 폭**이다(한글 글리프 자간이 라틴의 약 2배).
폰트 없이 계산되므로 스펙 파싱 단계에서 쓸 수 있다.
"""

import re
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
