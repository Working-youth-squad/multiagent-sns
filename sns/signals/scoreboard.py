"""알고리즘 신호 스코어보드 (FR-A1·A4, C8) — 순수·결정론, API 호출 없음.

플랫폼이 공식 발표한 랭킹 신호만 산식으로 옮긴다(근거: docs/research/
algorithm-signals-2026-08.md). 통설(최적 길이·해시태그 등)은 정의하지 않는다.
절대 임계값은 공식적으로 존재하지 않으므로 평가는 항상 **계정 자체 기준선
(중앙값) 대비**다. 결측은 어디서든 None — 0으로 채우는 순간이 오염이다(NFR-3).
"""

import json
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Literal

from sns.tools.contracts import Platform

# 기준선(중앙값) 성립 최소 표본 — 미만이면 전 신호 "판정 불가" (FR-A3).
MIN_BASELINE_N = 5
# 회귀/상관 허용 최소 표본 — 미만이면 small_sample=True (FR-A4: 30건 미만 회귀 금지).
MIN_REGRESSION_N = 30
# 리포트 상시 표기 문구 (FR-A4, 분산 연구 근거).
VARIANCE_WARNING = "동일 품질의 콘텐츠도 조회수가 10배 차이 날 수 있습니다(분산 연구 근거)."


@dataclass(frozen=True)
class SignalDef:
    """공식 랭킹 신호 1개 = metric_key 비율(또는 원값)."""

    name: str
    numerator: str  # metric_key (11-데이터모델 §4)
    denominator: str | None = None  # None = 원값 그대로
    higher_is_better: bool = True


# 플랫폼별 공식 신호 — 출처는 research 문서의 공식 발표 대응표와 1:1.
SIGNAL_DEFS: dict[Platform, tuple[SignalDef, ...]] = {
    "youtube": (
        SignalDef("engaged_rate", "engaged_views", "views"),  # 시청 선택률 근사
        SignalDef("avg_view_pct", "avg_view_pct"),  # 평균 시청 비율 (API 제공)
        SignalDef("avg_view_duration_s", "avg_view_duration_s"),
        SignalDef("likes_per_view", "likes", "views"),
        SignalDef("subscribers_gained", "subscribers_gained"),
    ),
    "instagram": (  # 정의만 — IG 폴러 구현은 adapters/instagram(팀원 소관)
        SignalDef("sends_per_reach", "shares", "reach"),  # Mosseri 공식 최중요 신호
        SignalDef("likes_per_reach", "likes", "reach"),
        SignalDef("saves_per_reach", "saved", "reach"),
        SignalDef("avg_watch_time_ms", "avg_watch_time_ms"),
        SignalDef("views", "views"),
    ),
}

Tag = Literal["above", "below", "at", "no_verdict"]


@dataclass(frozen=True)
class PostSignals:
    """게시물 1건의 신호값 — signal_values() 산출물."""

    post_id: str
    values: Mapping[str, float | None]


@dataclass(frozen=True)
class SignalRow:
    name: str
    value: float | None
    baseline: float | None  # 계정 중앙값 (표본 부족 시 None)
    tag: Tag


@dataclass(frozen=True)
class Scoreboard:
    platform: Platform
    post_id: str
    window_index: int
    sample_count: int  # baseline에 들어간 게시물 수
    rows: tuple[SignalRow, ...]
    verdict_available: bool  # sample_count >= MIN_BASELINE_N
    small_sample: bool  # sample_count < MIN_REGRESSION_N → 회귀/상관 금지
    variance_warning: str


def signal_values(
    platform: Platform, metrics: Mapping[str, float | None]
) -> dict[str, float | None]:
    """metric_key 값들 → 신호값. 분자/분모 결측·분모 0 → None (0 금지)."""
    out: dict[str, float | None] = {}
    for sig in SIGNAL_DEFS[platform]:
        num = metrics.get(sig.numerator)
        if sig.denominator is None:
            out[sig.name] = num
            continue
        den = metrics.get(sig.denominator)
        if num is None or den is None or den == 0:
            out[sig.name] = None
        else:
            out[sig.name] = num / den
    return out


def _tag(value: float | None, baseline: float | None, higher_is_better: bool) -> Tag:
    if value is None or baseline is None:
        return "no_verdict"
    if value == baseline:
        return "at"
    above = value > baseline
    return "above" if above == higher_is_better else "below"


def compute_scoreboard(
    platform: Platform,
    target: PostSignals,
    others: Sequence[PostSignals],
    *,
    window_index: int,
) -> Scoreboard:
    """target을 계정 기준선(others의 신호별 중앙값) 대비 태깅."""
    sample_count = len(others)
    verdict_available = sample_count >= MIN_BASELINE_N
    rows: list[SignalRow] = []
    for sig in SIGNAL_DEFS[platform]:
        value = target.values.get(sig.name)
        baseline: float | None = None
        if verdict_available:
            samples = [v for p in others if (v := p.values.get(sig.name)) is not None]
            # 신호별로도 유효 표본이 최소치를 넘어야 기준선 성립 (결측 게시물 왜곡 방지)
            if len(samples) >= MIN_BASELINE_N:
                baseline = statistics.median(samples)
        tag = _tag(value, baseline, sig.higher_is_better) if verdict_available else "no_verdict"
        rows.append(SignalRow(name=sig.name, value=value, baseline=baseline, tag=tag))
    return Scoreboard(
        platform=platform,
        post_id=target.post_id,
        window_index=window_index,
        sample_count=sample_count,
        rows=tuple(rows),
        verdict_available=verdict_available,
        small_sample=sample_count < MIN_REGRESSION_N,
        variance_warning=VARIANCE_WARNING,
    )


def common_elements(
    top_posts: Sequence[Mapping[str, str]], *, floor: int = MIN_BASELINE_N
) -> dict[str, str] | None:
    """상위 게시물들의 공통 속성(hook_pattern·topic·length_bucket 등) — 과반 최빈값만.

    표본 < floor → None ("판정 불가"). 어떤 속성도 과반이 아니면 빈 dict.
    """
    if len(top_posts) < floor:
        return None
    keys = {k for post in top_posts for k in post}
    out: dict[str, str] = {}
    for key in sorted(keys):
        values = [post[key] for post in top_posts if key in post]
        winner, count = Counter(values).most_common(1)[0]
        if count * 2 > len(top_posts):  # 과반일 때만 공통 요소로 인정
            out[key] = winner
    return out


def scoreboard_json(sb: Scoreboard) -> str:
    """단일 직렬화 지점 — 에이전트 툴 반환값이자 검증기의 허용 수치 출처."""
    return json.dumps(asdict(sb), ensure_ascii=False, sort_keys=True)
