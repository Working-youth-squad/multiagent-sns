"""LLM 분석글 검증기 (FR-L5, C7) — 정직 귀인 강제.

LLM은 서술만 담당한다는 경계선의 집행자. 분석글의 모든 수치는 스코어보드
JSON에 실재해야 하고, 언급된 post_id는 분석 대상이어야 하며, 표본 부족이면
그 사실을 글에 명시해야 한다. 위반 = 글 전체 거부(부분 수정 없음).
"""

import json
import re
from collections.abc import Collection
from dataclasses import dataclass

from sns.signals.scoreboard import VARIANCE_WARNING

# 서술에 등장해도 스코어보드 출처가 필요 없는 상수: 폴링 창(6·24·72h)·소표본 규칙(30건)·배수(10배).
ALLOWED_LITERALS = frozenset({6.0, 24.0, 72.0, 30.0, 10.0})
# 소표본일 때 글에 반드시 있어야 하는 표현.
INSUFFICIENT_PHRASES = ("판정 불가", "근거 부족")
# 분산 경고로 인정하는 최소 토큰 (VARIANCE_WARNING 전문 또는 핵심 표현).
VARIANCE_TOKEN = "10배"

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_YT_ID_RE = re.compile(r"\b[A-Za-z0-9_-]{11}\b")


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reasons: tuple[str, ...]


def _numbers_in(obj: object, acc: set[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        acc.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers_in(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _numbers_in(v, acc)


def _cited(n: float, allowed: set[float]) -> bool:
    """n이 허용 집합의 수를 0~2자리 반올림(원값 또는 ×100 백분율)한 값인가."""
    for a in allowed:
        for scaled in (a, a * 100):
            for digits in (0, 1, 2):
                if n == round(scaled, digits):
                    return True
    return False


def validate_analysis(
    body: str,
    *,
    scoreboard_json: str,
    post_ids: Collection[str],
    verdict_available: bool,
) -> ValidationResult:
    """분석글 정직성 검사 — 실패 사유를 전부 수집해 반환."""
    reasons: list[str] = []

    allowed: set[float] = set(ALLOWED_LITERALS)
    _numbers_in(json.loads(scoreboard_json), allowed)
    # post_id형 토큰 내부의 숫자(예: dQw4w9WgXcQ의 4·9)는 수치 인용 검사 대상이 아님.
    body_wo_ids = _YT_ID_RE.sub(" ", body)
    for token in _NUMBER_RE.findall(body_wo_ids):
        n = float(token)
        if not _cited(n, allowed):
            reasons.append(f"인용 없는 수치: {token}")

    for candidate in _YT_ID_RE.findall(body):
        # 11자 영단어(예: 'performance') 오탐 방지 — 숫자·기호가 섞인 토큰만 id로 간주.
        if candidate.isalpha():
            continue
        if candidate not in post_ids:
            reasons.append(f"실재하지 않는 post_id: {candidate}")

    if not verdict_available and not any(p in body for p in INSUFFICIENT_PHRASES):
        reasons.append("표본 부족인데 '판정 불가'/'근거 부족' 미표기")

    if VARIANCE_TOKEN not in body and VARIANCE_WARNING not in body:
        reasons.append("분산 경고 문구 누락 (FR-A4 상시 표기)")

    return ValidationResult(ok=not reasons, reasons=tuple(reasons))
