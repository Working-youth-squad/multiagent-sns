"""이미지 안전성 게이트 (FR-Q7) — 금지 소재 차단 + 결정론 선택.

FR-Q7은 금지어/카테고리를 **코드 상수로 외부화**하고 같은 입력이 같은 판정을 내도록
요구한다. 그래서 여기엔 LLM이 없다: 목록과 규칙뿐이고, 판정은 재실행해도 같다.
(FR-Q7의 LLM 이중 검사는 텍스트 쪽 후속이다 — 이미지에서 막아야 할 것은 대부분
목록으로 잡힌다.)

막는 것과 이유:
  nsfw       — 계정 정지로 직행하는 유일한 범주.
  political  — 실험 설계상 정치 편향은 금지 소재.
  people     — 모르는 사람 얼굴이 배경에 깔릴 이유가 없다. Pexels 라이선스도
               "식별 가능한 인물을 불리하게 비치도록 쓰지 말 것"을 건다.
  brand      — 타사 로고는 저작권·상표 문제라 별도 트랙(공식 로고)에서 근거를 갖고 쓴다.
  violence   — 개발 채널 배경으로 부적절하고 플랫폼 제재 소재다.

**질의는 영문만 받는다.** 검색과 alt가 영어라 금지어 매칭도 영어 기준이다 — 한글 질의를
허용하면 게이트를 그냥 지나간다.
"""

import re
from dataclasses import dataclass

# 소스별 라이선스 화이트리스트. 소스가 늘어날 때 확인 없이 흘러드는 걸 막는 자물쇠다.
#   pexels — Pexels License: 상업 이용 허용, 크레딧 불필요.
ALLOWED_LICENSES: frozenset[str] = frozenset({"pexels"})

# 940 정사각으로 늘렸을 때 버티는 최소 변. square.MIN_SOURCE_SIDE와 같은 근거지만,
# 이쪽은 **다운로드 전에** 메타데이터만 보고 거르는 자리라 따로 둔다.
MIN_ACCEPTABLE_SIDE = 640

BLOCKED_TERMS: dict[str, tuple[str, ...]] = {
    "nsfw": ("nude", "nudity", "naked", "erotic", "sexy", "lingerie", "bikini", "porn"),
    "political": ("politician", "election", "protest", "president", "campaign", "parliament"),
    "people": ("man", "woman", "person", "people", "portrait", "face", "crowd", "model"),
    "brand": ("logo", "brand", "trademark", "billboard"),
    "violence": ("gun", "rifle", "weapon", "war", "soldier", "blood", "corpse"),
}


@dataclass(frozen=True)
class StockImage:
    """스톡 검색 결과 1건. 소스가 달라도 이 모양으로 정규화해 게이트에 넣는다."""

    source: str  # "pexels"
    source_id: str
    page_url: str  # 출처 페이지 — 사후 확인용으로 남긴다
    download_url: str
    width: int
    height: int
    alt: str
    photographer: str
    license_id: str


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str = ""


def _blocked_term(text: str) -> tuple[str, str] | None:
    """(카테고리, 걸린 단어) — 단어 경계로 맞춘다. 'man'이 'management'를 막으면 안 된다."""
    lowered = text.lower()
    for category, terms in BLOCKED_TERMS.items():
        for term in terms:
            if re.search(rf"\b{re.escape(term)}\b", lowered):
                return category, term
    return None


def screen_query(query: str) -> Verdict:
    """검색어 검열 — 금지 소재를 **검색하기 전에** 끊는다."""
    if not query.strip():
        raise ValueError("검색어는 비지 않은 문자열이어야 함")
    if not query.isascii():
        return Verdict(False, "검색어는 영문이어야 함 — 금지어 판정이 영어 기준이다")
    hit = _blocked_term(query)
    if hit:
        return Verdict(False, f"금지 소재({hit[0]}) 검색어: {hit[1]}")
    return Verdict(True)


def screen_image(candidate: StockImage) -> Verdict:
    """후보 1건 검열 — 라이선스·해상도·설명문(alt)."""
    if candidate.license_id not in ALLOWED_LICENSES:
        return Verdict(False, f"허용되지 않은 라이선스: {candidate.license_id!r}")
    if min(candidate.width, candidate.height) < MIN_ACCEPTABLE_SIDE:
        return Verdict(
            False,
            f"해상도 미달 — 짧은 변 {MIN_ACCEPTABLE_SIDE}px 이상 필요: "
            f"{candidate.width}×{candidate.height}",
        )
    # alt가 비어 오는 건 흔하다. 없다고 떨어뜨리면 공급이 말라붙는다.
    hit = _blocked_term(candidate.alt) if candidate.alt else None
    if hit:
        return Verdict(False, f"금지 소재({hit[0]}) 설명문: {hit[1]}")
    return Verdict(True)


def _rank(candidate: StockImage) -> tuple[float, int, str]:
    """정사각에 가까울수록, 그 다음 클수록 앞. 동점은 id로 갈라 순서 의존을 없앤다."""
    long_side = max(candidate.width, candidate.height)
    short_side = max(min(candidate.width, candidate.height), 1)
    return (long_side / short_side, -long_side, candidate.source_id)


def pick_image(candidates: list[StockImage]) -> StockImage | None:
    """게이트를 통과한 후보 중 하나 — 결정론. 통과가 없으면 None(그라데이션 폴백)."""
    allowed = [c for c in candidates if screen_image(c).allowed]
    if not allowed:
        return None
    return min(allowed, key=_rank)
