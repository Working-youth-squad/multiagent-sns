"""보상 산식 + 보상 배치 (FR-L2, M6) — 관측창 하나를 `float | None`으로 접는다.

밴딧(FR-L3)과 플레이북(FR-L4)이 읽는 `topic_stats`는 결국 이 한 숫자의 합이다. 그래서
여기서 조용히 틀리면 학습 전체가 조용히 틀린다 — 이 모듈이 지키는 것은 정확도가 아니라
**정직성**이다.

## 세 가지 규율

1. **결측을 0으로 접지 않는다**(NFR-3). 값이 없는 항은 합에서 빠지고, 남은 항의 가중치로
   다시 나눈다. 0으로 채우면 "지표를 안 준 게시물"이 "성과가 나쁜 게시물"로 둔갑한다.
2. **표본이 모자라면 `None`**(FR-L2). 1차 항의 가중치 중 절반도 못 채우면 값을 내지
   않는다. `None`과 `0.0`은 다른 사실이다 — 앞은 표본이 아니고(밴딧 집계에서 빠진다),
   뒤는 "성과가 0"이다.
3. **산식은 공식 신호와 같은 방향으로**(FR-L2 v2). 항은 `sns/signals/scoreboard.py`의
   `SIGNAL_DEFS`를 그대로 참조한다. 스코어보드가 "공유율"이라 부르는 것과 보상이 세는
   것이 갈리면, 리포트와 학습이 다른 말을 하게 된다.

## 왜 비율인가, 조회수는 왜 log인가

원값을 그냥 더하면 단위가 큰 항이 산식을 삼킨다(`avg_watch_time_ms`는 만 단위, 공유율은
0.0x다). 그래서 모든 항은 **[0,1]로 접힌 뒤** 합쳐진다: 비율은 그대로(1 초과는 절단),
백분율은 ÷100, 개수·시간은 `log1p(x)/log1p(기준값)`.

조회수가 log 보조인 이유는 heavy-tail이다(FR-L2). 한 건이 10배 터지면 선형 합에서는 그
한 건이 평균 보상을 정의해 버리고, 밴딧은 그 arm이 좋아서가 아니라 **운이 좋아서** 이긴
것을 학습한다. log는 그 꼬리를 눌러 준다. 같은 이유로 조회수 항은 `primary=False`다 —
합에는 들어가되 **"판정할 만큼 봤다"의 근거로는 세지 않는다**. 조회수만 있는 관측은
표본 부족이다.

## 계수는 아직 없다

`FORMULA_VERSION = "v0-unweighted"` — 항의 **구성과 방향만** 확정하고 계수는 전부 1.0이다.
기획서가 "계수는 M1 실측 후 사전등록"(FR-L2)이라 지금 임의 수를 넣으면 그게 사전등록으로
굳는다. 실측이 끝나면 아래 **계수 블록 하나만** 갈아 끼우고 버전 문자열을 올린다 — 버전이
바뀌면 배치가 알아서 전건 재계산한다(`run_reward_batch`).

## 버전 문자열에 goal이 붙는 이유

같은 관측이라도 goal이 다르면 다른 숫자가 나온다(`REWARD_SPECS`가 goal별로 갈린다).
`reward.formula_version`이 산식만 적고 goal을 빼면, 서로 다른 goal의 보상이 한
`topic_stats` 칸에 섞여도 아무도 모른다. 그래서 저장되는 값은 `v0-unweighted+goal`이다.

⚠️ 한계: `topic_stats`는 (주제×포맷×채널)만 가르고 goal은 가르지 않는다. 실험 중간에
goal을 바꾸면 그 칸의 `reward_sum`은 두 산식의 혼합이 된다 — 지금은 한 라운드에 goal
하나를 고정하는 운영으로 막는다(FR-E).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite, log1p
from typing import Literal

from sns.goals import DEFAULT_GOAL_REF, GOAL_PRESETS, GoalRef, resolve_goal
from sns.learning.observations import as_metric_map
from sns.learning.schedule import REWARD_WINDOW_INDEX
from sns.learning.stores import MetricStore
from sns.signals.scoreboard import SIGNAL_DEFS, signal_values
from sns.tools.contracts import Platform

# 항의 원값을 [0,1]로 접는 방법. "rate"=비율 그대로, "pct"=백분율(÷100),
# "log"=개수·시간(log1p ÷ log1p(기준값)) — 어느 쪽이든 1을 넘으면 절단된다.
Transform = Literal["rate", "pct", "log"]
# 항이 읽는 곳. 기본은 스코어보드 신호이고, 스코어보드에 정의가 없는 것만 원 metric_key.
TermSource = Literal["signal", "metric"]


@dataclass(frozen=True)
class RewardTerm:
    """산식의 한 항 = (무엇을 읽고, 어떻게 접고, 얼마나 세는가)."""

    key: str
    weight: float
    transform: Transform = "rate"
    # transform="log"일 때 1.0으로 포화되는 기준값 — "이 정도면 만점"의 눈금.
    log_ref: float | None = None
    # False = 보조 항. 합에는 들어가지만 표본 충분 판정에는 세지 않는다(모듈 docstring).
    primary: bool = True
    source: TermSource = "signal"

    def __post_init__(self) -> None:
        if self.weight <= 0:
            raise ValueError(f"가중치는 양수: {self!r}")
        if (self.transform == "log") != (self.log_ref is not None):
            raise ValueError(f"log 항에만 log_ref가 있어야 한다: {self!r}")
        if self.log_ref is not None and self.log_ref <= 0:
            raise ValueError(f"log_ref는 양수: {self!r}")


# ── 계수 블록 — 사전등록 대상(FR-L2). 실측 후 여기만 갈아 끼운다 ─────
#
# v0에서 가중치는 **전부 1.0**이다. 지금 손으로 정한 수는 근거가 없고, 근거 없는 수가
# 한 번 저장되면 그 뒤의 모든 비교가 그 수를 기준으로 서기 때문이다. 정해진 것은 항의
# 구성(무엇을 보는가)과 방향(클수록 좋은가)뿐 — 그 둘은 플랫폼 공식 발표에서 왔다.

FORMULA_VERSION = "v0-unweighted"

# 1차 항 가중치 중 이만큼은 실제 값이 있어야 보상을 낸다. 절반인 이유: 한 항만 남은
# 평균은 그 항의 다른 이름일 뿐인데, 그것이 세 항짜리 보상과 같은 칸에서 비교된다.
MIN_PRIMARY_COVERAGE = 0.5

# log 항의 "만점" 눈금. 계정 규모에 따라 갈리므로 실측 후 조정 대상이다.
VIEWS_LOG_REF = 10_000.0
WATCH_MS_LOG_REF = 30_000.0  # 30초 — 릴스 평균 시청시간
DURATION_S_LOG_REF = 60.0  # 쇼츠 1분
SUBSCRIBERS_LOG_REF = 10.0  # 한 건에서 구독 10 = 이례적 성과


def _views_term(*, primary: bool) -> RewardTerm:
    """조회수 항 — 두 플랫폼 공통.

    `source="metric"`인 이유: IG에는 `views` 신호가 있지만 YT `SIGNAL_DEFS`에는 없다
    (분모로만 쓰인다). 원 metric_key는 두 플랫폼 모두 `views`라 한 자리로 모은다.
    """
    return RewardTerm(
        key="views",
        weight=1.0,
        transform="log",
        log_ref=VIEWS_LOG_REF,
        primary=primary,
        source="metric",
    )


# goal × 플랫폼 → 항 구성. 신호 이름은 SIGNAL_DEFS와 1:1이고, 없는 신호는 import 시점에
# _validate_specs()가 잡는다(계수 블록의 오타가 조용히 결측으로 새지 않게).
REWARD_SPECS: dict[GoalRef, dict[Platform, tuple[RewardTerm, ...]]] = {
    # 도달 성장 — 조회수 자체가 목표라 여기서만 views가 1차 항이다.
    "reach_growth": {
        "instagram": (
            _views_term(primary=True),
            # 공유는 IG가 공식 최중요 신호로 밝힌 도달 경로(Mosseri) — 도달의 원인 쪽.
            RewardTerm(key="sends_per_reach", weight=1.0),
        ),
        "youtube": (
            _views_term(primary=True),
            RewardTerm(key="engaged_rate", weight=1.0),
        ),
    },
    # 팔로워·구독 전환 — per-post 선행 근사(goals.py 참조). IG 계정 팔로워는 관측 밖.
    "follower_growth": {
        "instagram": (
            RewardTerm(key="saves_per_reach", weight=1.0),
            RewardTerm(key="sends_per_reach", weight=1.0),
            _views_term(primary=False),
        ),
        "youtube": (
            RewardTerm(
                key="subscribers_gained",
                weight=1.0,
                transform="log",
                log_ref=SUBSCRIBERS_LOG_REF,
            ),
            _views_term(primary=False),
        ),
    },
    # 참여 깊이 — 능동 참여(저장·공유)가 수동 참여(좋아요)보다 앞이지만, v0에서는
    # 그 순서를 가중치로 표현하지 않는다(근거가 실측 뒤에 온다).
    "engagement_depth": {
        "instagram": (
            RewardTerm(key="sends_per_reach", weight=1.0),
            RewardTerm(key="saves_per_reach", weight=1.0),
            RewardTerm(key="likes_per_reach", weight=1.0),
            _views_term(primary=False),
        ),
        "youtube": (
            RewardTerm(key="engaged_rate", weight=1.0),
            RewardTerm(key="likes_per_view", weight=1.0),
            _views_term(primary=False),
        ),
    },
    # 시청 유지 — IG `skip_rate`는 여기 없다: 스코어보드에 정의가 없고(도입 중),
    # 낮을수록 좋은 유일한 신호라 방향 반전이 필요하다. 관측이 쌓이면 그때 넣는다.
    "watch_through": {
        "instagram": (
            RewardTerm(
                key="avg_watch_time_ms",
                weight=1.0,
                transform="log",
                log_ref=WATCH_MS_LOG_REF,
            ),
            _views_term(primary=False),
        ),
        "youtube": (
            RewardTerm(key="avg_view_pct", weight=1.0, transform="pct"),
            RewardTerm(key="engaged_rate", weight=1.0),
            RewardTerm(
                key="avg_view_duration_s",
                weight=1.0,
                transform="log",
                log_ref=DURATION_S_LOG_REF,
            ),
            _views_term(primary=False),
        ),
    },
}

# ── 산식 ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RewardBreakdown:
    """보상 1건의 계산 내역 — 왜 그 숫자인지(또는 왜 `None`인지) 말할 수 있게.

    저장되는 것은 `value` 하나지만, 배치 dry-run과 테스트는 나머지를 읽는다. 값만
    돌려주면 "표본 부족"과 "전부 0"을 콘솔에서 구분할 수 없다.
    """

    value: float | None
    coverage: float  # 값이 있는 1차 항의 가중치 비율
    used: tuple[str, ...]  # 합에 들어간 항
    missing: tuple[str, ...]  # 결측이라 빠진 항
    reason: str | None = None  # value=None의 사유(원장·콘솔용)


def formula_version(goal_ref: GoalRef | str) -> str:
    """`reward.formula_version`에 저장되는 문자열 — 산식 버전 + goal(모듈 docstring)."""
    return f"{FORMULA_VERSION}+{resolve_goal(goal_ref).ref}"


def _fold(term: RewardTerm, value: float) -> float:
    """항의 원값 → [0,1]. 절단이 heavy-tail 방어의 마지막 단이다."""
    if term.transform == "log":
        assert term.log_ref is not None  # __post_init__이 강제
        folded = log1p(max(value, 0.0)) / log1p(term.log_ref)
    elif term.transform == "pct":
        folded = value / 100.0
    else:
        folded = value
    return min(max(folded, 0.0), 1.0)


def _term_value(
    term: RewardTerm,
    signals: Mapping[str, float | None],
    metrics: Mapping[str, float | None],
) -> float | None:
    source = signals if term.source == "signal" else metrics
    value = source.get(term.key)
    # NaN·inf는 결측으로 본다 — 어댑터가 0/0을 넘겨도 합이 오염되지 않게.
    return None if value is None or not isfinite(value) else value


def compute_reward(
    *,
    goal_ref: GoalRef | str,
    platform: Platform,
    metrics: Mapping[str, float | None],
) -> RewardBreakdown:
    """관측창 하나(metric_key → 값|None) → 보상 내역. 순수·결정론(NFR-2).

    입력은 `as_metric_map()`이 낸 모양 그대로다 — 결측 키는 **빠지지 않고 None으로**
    들어온다. 키가 아예 없는 것(그 지표를 안 본다)과 같게 취급하되, 둘 다 항이
    빠지는 것이지 0이 되는 것이 아니다.
    """
    goal = resolve_goal(goal_ref)
    terms = REWARD_SPECS[goal.ref][platform]
    signals = signal_values(platform, metrics)

    weighted_sum = 0.0
    used_weight = 0.0
    primary_total = 0.0
    primary_present = 0.0
    used: list[str] = []
    missing: list[str] = []

    for term in terms:
        if term.primary:
            primary_total += term.weight
        value = _term_value(term, signals, metrics)
        if value is None:
            missing.append(term.key)
            continue
        if term.primary:
            primary_present += term.weight
        weighted_sum += term.weight * _fold(term, value)
        used_weight += term.weight
        used.append(term.key)

    coverage = primary_present / primary_total if primary_total else 0.0
    if not used:
        # 창은 찍혔는데 쓸 값이 하나도 없다 — 폴링이 안 된 것과는 다른 사실이다.
        return RewardBreakdown(None, coverage, (), tuple(missing), "no_signals")
    if coverage < MIN_PRIMARY_COVERAGE:
        return RewardBreakdown(
            None, coverage, tuple(used), tuple(missing), "insufficient_primary_signals"
        )
    return RewardBreakdown(weighted_sum / used_weight, coverage, tuple(used), tuple(missing))


def reward_value(
    *,
    goal_ref: GoalRef | str,
    platform: Platform,
    metrics: Mapping[str, float | None],
) -> float | None:
    """FR-L2의 `RewardFn(관측창) → float | None` 그 자체 — 내역이 필요 없는 호출부용."""
    return compute_reward(goal_ref=goal_ref, platform=platform, metrics=metrics).value


# ── 배치 ────────────────────────────────────────────────────────────

# 한 번 훑을 때 되돌아보는 기간. 대표 창이 72h라 3일이면 충분해 보이지만, 배치가 며칠
# 멈췄다 살아나는 일이 정상 운영이다. 지평을 안 두면(since=None) `published_items`가
# 항상 가장 오래된 200건만 돌려줘 원장이 자란 뒤 새 건이 영영 안 잡힌다.
DEFAULT_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class RewardBatchReport:
    """한 번 훑은 결과 — 운영자가 콘솔에서 읽고, 테스트가 겨누는 요약."""

    items: int = 0  # 훑은 발행 건
    computed: int = 0  # 값이 확정돼 저장된 건
    insufficient: int = 0  # 표본 부족 → NULL로 저장된 건(학습 제외)
    pending: int = 0  # 대표 창이 아직 안 찍힌 건 — 폴러를 기다린다
    absorbed: int = 0  # 같은 산식으로 이미 계산돼 있어 건너뛴 건

    def summary(self) -> str:
        return (
            f"대상 {self.items}건 · 확정 {self.computed} · 부족 {self.insufficient} · "
            f"대기 {self.pending} · 흡수 {self.absorbed}"
        )


def run_reward_batch(
    *,
    store: MetricStore,
    now: datetime,
    goal_ref: GoalRef | str = DEFAULT_GOAL_REF,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    limit: int = 200,
    recompute: bool = False,
) -> RewardBatchReport:
    """대표 창(72h)이 찍힌 발행 건의 보상을 계산해 적재한다. `now`는 주입(NFR-2).

    `save_reward`가 `topic_stats`를 함께 갱신하므로 **여기서 통계를 더하지 않는다** —
    호출부가 따로 더하면 같은 성과가 두 번 세어진다(`stores.py` 참조).

    이미 같은 `formula_version`으로 계산된 건은 건너뛴다. 대표 창의 관측은 한 번
    찍히면 바뀌지 않으므로(멱등 저장), 같은 산식으로 다시 계산해도 같은 값이 나온다.
    산식 버전이 올라가면 자동으로 전건 재계산이 된다 — 그게 사전등록의 실행 경로다.
    """
    version = formula_version(goal_ref)
    items = store.published_items(since=now - timedelta(days=lookback_days), limit=limit)
    report = RewardBatchReport(items=len(items))

    for item in items:
        if REWARD_WINDOW_INDEX not in item.observed_windows:
            report = _bump(report, pending=1)
            continue
        existing = store.read_reward(item.publication_id)
        if existing is not None and existing[1] == version and not recompute:
            report = _bump(report, absorbed=1)
            continue
        breakdown = compute_reward(
            goal_ref=goal_ref,
            platform=item.platform,
            metrics=observed_metrics(store, item.publication_id),
        )
        store.save_reward(
            publication_id=item.publication_id,
            reward_value=breakdown.value,
            formula_version=version,
        )
        if breakdown.value is None:
            report = _bump(report, insufficient=1)
        else:
            report = _bump(report, computed=1)

    if report.computed or report.insufficient:
        # 한 번에 한 줄만 남긴다. 건마다 남기면 "표본 부족"이라는 사실이 원장에 두 벌로
        # 생긴다 — 그 사실의 정본은 `reward.reward_value IS NULL`이다.
        store.log_event(
            cycle_id=None,
            kind="notice",
            payload={
                "reason": "reward_batch",
                "formula_version": version,
                "computed": report.computed,
                "insufficient": report.insufficient,
                "pending": report.pending,
            },
        )
    return report


def observed_metrics(store: MetricStore, publication_id: str) -> Mapping[str, float | None]:
    """대표 창의 관측 → 산식 입력. 배치와 dry-run이 같은 자리에서 읽게.

    `StoredMetrics`(observations.py)를 쓰지 않는 이유: 그쪽은 `PollMetrics` 계약을
    되먹이느라 `(platform, post_id)`로 말하는데, 배치는 이미 `publication_id`를 손에
    들고 있다. 색인을 한 번 더 만들 이유가 없다. "관측 → 지표" 변환은 두 경로 모두
    `as_metric_map` 하나를 쓴다.
    """
    return as_metric_map(
        store.read_observation(publication_id=publication_id, window_index=REWARD_WINDOW_INDEX)
    )


def _bump(
    report: RewardBatchReport,
    *,
    computed: int = 0,
    insufficient: int = 0,
    pending: int = 0,
    absorbed: int = 0,
) -> RewardBatchReport:
    return RewardBatchReport(
        items=report.items,
        computed=report.computed + computed,
        insufficient=report.insufficient + insufficient,
        pending=report.pending + pending,
        absorbed=report.absorbed + absorbed,
    )


def _validate_specs() -> None:
    """계수 블록의 오타를 import 시점에 잡는다.

    없는 신호 이름은 조용히 결측이 되고, 결측은 표본 부족으로 흘러 `None` 보상이 된다 —
    산식이 통째로 죽어도 테이블에는 NULL만 늘어 아무도 눈치채지 못한다.
    """
    for goal_ref in GOAL_PRESETS:
        specs = REWARD_SPECS.get(goal_ref)
        if not specs:
            raise ValueError(f"goal 프리셋에 대응하는 산식이 없다: {goal_ref}")
        for platform, terms in specs.items():
            names = {sig.name for sig in SIGNAL_DEFS[platform]}
            keys = {sig.numerator for sig in SIGNAL_DEFS[platform]} | {
                sig.denominator for sig in SIGNAL_DEFS[platform] if sig.denominator
            }
            for term in terms:
                known = names if term.source == "signal" else keys
                if term.key not in known:
                    raise ValueError(
                        f"{goal_ref}/{platform}: 정의 없는 {term.source} '{term.key}' "
                        f"(sns/signals/scoreboard.py 참조)"
                    )
            if not any(term.primary for term in terms):
                raise ValueError(f"{goal_ref}/{platform}: 1차 항이 없다 — 전건 표본 부족이 된다")


_validate_specs()
