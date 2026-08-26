"""보상 산식·배치 (FR-L2, M6) — InMemory 저장소로 DB 없이.

겨누는 것은 "숫자가 예쁜가"가 아니다. 계수가 아직 사전등록 전이라(v0-unweighted) 값
자체는 바뀔 예정이고, 바뀌어도 무너지면 안 되는 것은 **결측을 어떻게 다루는가**와
**재계산이 통계를 부풀리지 않는가** 둘이다. 이 파일은 그 둘을 붙잡는다.
"""

from datetime import UTC, datetime, timedelta

import pytest

from sns.goals import GOAL_PRESETS
from sns.learning.reward import (
    FORMULA_VERSION,
    MIN_PRIMARY_COVERAGE,
    REWARD_SPECS,
    RewardTerm,
    compute_reward,
    formula_version,
    reward_value,
    run_reward_batch,
)
from sns.learning.schedule import REWARD_WINDOW_INDEX
from sns.learning.stores import InMemoryMetricStore, PublishedItem
from sns.signals.scoreboard import SIGNAL_DEFS
from sns.tools.contracts import MetricValue, Platform

NOW = datetime(2026, 8, 27, 0, 0, tzinfo=UTC)
GOAL = "engagement_depth"

# 참여 깊이(IG) 산식이 보는 것 전부 — 여기서 키를 하나씩 빼며 결측을 만든다.
FULL_IG = {"reach": 1000.0, "shares": 30.0, "saved": 50.0, "likes": 200.0, "views": 5000.0}


def _metrics(**overrides: float | None) -> dict[str, float | None]:
    return {**FULL_IG, **overrides}


# ── 결측 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("overrides", "expect_value"),
    [
        pytest.param({}, True, id="전부 있음"),
        pytest.param({"likes": None}, True, id="1차 하나 결측 — 나머지로 판정"),
        pytest.param({"likes": None, "saved": None}, False, id="1차 하나만 남음 — 문턱 미달"),
        pytest.param({"shares": None, "saved": None, "likes": None}, False, id="1차 전멸"),
        pytest.param({"reach": None}, False, id="분모 결측 — 비율 셋이 함께 죽는다"),
        pytest.param(dict.fromkeys(FULL_IG), False, id="전부 결측"),
    ],
)
def test_결측_조합별_판정(overrides: dict[str, float | None], expect_value: bool) -> None:
    result = compute_reward(goal_ref=GOAL, platform="instagram", metrics=_metrics(**overrides))
    assert (result.value is not None) is expect_value
    if not expect_value:
        assert result.reason is not None  # 왜 NULL인지 말할 수 있어야 한다


def test_전부_결측이면_None이고_사유가_남는다() -> None:
    result = compute_reward(goal_ref=GOAL, platform="instagram", metrics=dict.fromkeys(FULL_IG))
    assert result.value is None
    assert result.reason == "no_signals"
    assert result.used == ()


def test_조회수만_있으면_표본이_아니다() -> None:
    """views는 보조 항 — 합에는 들어가도 "판정할 만큼 봤다"의 근거는 못 된다."""
    result = compute_reward(
        goal_ref=GOAL, platform="instagram", metrics=_metrics(reach=None, views=5000.0)
    )
    assert result.value is None
    assert result.reason == "insufficient_primary_signals"
    assert "views" in result.used  # 값은 읽었으되 판정에는 못 쓴다
    assert result.coverage == 0.0


def test_결측은_0이_아니라_제외다() -> None:
    """좋아요가 결측인 게시물이, 좋아요가 실제 0인 게시물보다 낮게 나오면 안 된다."""
    missing = reward_value(goal_ref=GOAL, platform="instagram", metrics=_metrics(likes=None))
    zeroed = reward_value(goal_ref=GOAL, platform="instagram", metrics=_metrics(likes=0.0))
    assert missing is not None and zeroed is not None
    assert missing > zeroed


def test_성과_0은_None이_아니다() -> None:
    """전 지표가 실제 0인 게시물은 표본이다 — 0.0으로 학습에 들어간다."""
    result = compute_reward(
        goal_ref=GOAL,
        platform="instagram",
        metrics={"reach": 1000.0, "shares": 0.0, "saved": 0.0, "likes": 0.0, "views": 0.0},
    )
    assert result.value == 0.0
    assert result.reason is None


def test_NaN은_결측으로_본다() -> None:
    """어댑터가 0/0을 흘려도 합이 오염되지 않는다."""
    result = compute_reward(
        goal_ref=GOAL, platform="instagram", metrics=_metrics(likes=float("nan"))
    )
    assert result.value is not None
    assert "likes_per_reach" in result.missing


def test_경계는_포함이다() -> None:
    """coverage == MIN_PRIMARY_COVERAGE는 통과 — 산식의 문턱이 부등호 하나로 뒤집히지 않게.

    reach_growth(IG)는 1차 항이 둘(views·sends_per_reach)이라 하나만 결측이면 정확히 0.5다.
    """
    result = compute_reward(
        goal_ref="reach_growth",
        platform="instagram",
        metrics={"views": 5000.0, "reach": None, "shares": None},
    )
    assert result.coverage == pytest.approx(MIN_PRIMARY_COVERAGE)
    assert result.value is not None


# ── 접기(heavy-tail 방어·단위) ──────────────────────────────────────


def test_조회수는_log로_눌린다() -> None:
    """10배 터진 건이 보상을 10배 가져가면 밴딧은 운을 학습한다(FR-L2)."""
    base = reward_value(goal_ref="reach_growth", platform="youtube", metrics={"views": 1_000.0})
    viral = reward_value(goal_ref="reach_growth", platform="youtube", metrics={"views": 10_000.0})
    assert base is not None and viral is not None
    assert viral > base
    assert viral < base * 2  # log — 선형이었다면 10배다


def test_값은_0과_1_사이로_접힌다() -> None:
    """단위가 큰 항이 산식을 삼키지 않게 — 백만 조회도 1을 넘지 않는다."""
    for metrics in (
        {"views": 10_000_000.0},
        {"views": 1.0},
    ):
        value = reward_value(goal_ref="reach_growth", platform="youtube", metrics=metrics)
        assert value is not None and 0.0 <= value <= 1.0


def test_백분율은_100으로_나눈다() -> None:
    """YT avg_view_pct는 0~100 스케일 — 그대로 더하면 다른 항이 안 보인다."""
    result = compute_reward(
        goal_ref="watch_through",
        platform="youtube",
        metrics={"avg_view_pct": 100.0, "engaged_views": 10.0, "views": 10.0},
    )
    assert result.value is not None and result.value <= 1.0


# ── goal ────────────────────────────────────────────────────────────


def test_goal마다_다른_것을_본다() -> None:
    """시청 지표만 있는 관측은 watch_through에선 표본이고 engagement_depth에선 아니다."""
    metrics: dict[str, float | None] = {
        "avg_view_pct": 60.0,
        "avg_view_duration_s": 30.0,
        "views": 500.0,
        "engaged_views": None,
        "likes": None,
    }
    assert reward_value(goal_ref="watch_through", platform="youtube", metrics=metrics) is not None
    assert reward_value(goal_ref="engagement_depth", platform="youtube", metrics=metrics) is None


def test_산식_버전에_goal이_박힌다() -> None:
    """goal이 다르면 다른 숫자다 — 버전이 같으면 한 칸에 섞여도 아무도 모른다."""
    assert formula_version("watch_through") == f"{FORMULA_VERSION}+watch_through"
    assert formula_version("reach_growth") != formula_version("watch_through")


def test_미등록_goal은_거부된다() -> None:
    with pytest.raises(ValueError, match="알 수 없는 goal_ref"):
        compute_reward(goal_ref="viral_growth", platform="instagram", metrics=FULL_IG)


# ── 계수 블록의 무결성 ──────────────────────────────────────────────


def test_모든_goal_프리셋에_산식이_있다() -> None:
    """goal은 늘었는데 산식이 안 따라오면 그 goal의 전 발행분이 조용히 NULL이 된다."""
    for goal_ref in GOAL_PRESETS:
        assert set(REWARD_SPECS[goal_ref]) == {"instagram", "youtube"}


def test_모든_항이_스코어보드에_정의돼_있다() -> None:
    """오타 난 신호 이름은 영원히 결측이고, 결측은 조용히 NULL 보상이 된다."""
    for specs in REWARD_SPECS.values():
        for platform, terms in specs.items():
            names = {sig.name for sig in SIGNAL_DEFS[platform]}
            keys = {sig.numerator for sig in SIGNAL_DEFS[platform]} | {
                sig.denominator for sig in SIGNAL_DEFS[platform] if sig.denominator
            }
            for term in terms:
                assert term.key in (names if term.source == "signal" else keys)


def test_v0_계수는_전부_1이다() -> None:
    """실측 전 임의 계수를 금지한다(FR-L2) — 이 테스트가 깨지면 사전등록 문서가 먼저다."""
    assert FORMULA_VERSION == "v0-unweighted"
    for specs in REWARD_SPECS.values():
        for terms in specs.values():
            assert {term.weight for term in terms} == {1.0}


def test_log_항은_기준값이_있어야_한다() -> None:
    with pytest.raises(ValueError, match="log_ref"):
        RewardTerm(key="views", weight=1.0, transform="log")
    with pytest.raises(ValueError, match="log_ref"):
        RewardTerm(key="views", weight=1.0, log_ref=10.0)


# ── 배치 ────────────────────────────────────────────────────────────


def _store(*, platform: Platform = "instagram", published_h_ago: int = 80) -> InMemoryMetricStore:
    store = InMemoryMetricStore()
    store.add_published_item(
        PublishedItem(
            publication_id="pub-1",
            platform=platform,
            external_post_id="post-1",
            published_at=NOW - timedelta(hours=published_h_ago),
            content_format="reels" if platform == "instagram" else "shorts",
            topic_id="topic-1",
            channel_mode="auto",
        )
    )
    return store


def _observe(store: InMemoryMetricStore, **metrics: float | None) -> None:
    store.save_observation(
        publication_id="pub-1",
        window_index=REWARD_WINDOW_INDEX,
        values=tuple(
            MetricValue(key, value, value is None) for key, value in (metrics or FULL_IG).items()
        ),
        observed_at=NOW,
    )


def test_대표_창이_없으면_기다린다() -> None:
    """72h 창 전에 6h 관측만 있다고 보상을 내면, 초기 곡선을 최종 성과로 굳힌다."""
    store = _store()
    store.save_observation(
        publication_id="pub-1", window_index=0, values=(MetricValue("views", 10.0, False),)
    )
    report = run_reward_batch(store=store, now=NOW, goal_ref=GOAL)
    assert (report.pending, report.computed) == (1, 0)
    assert store.read_reward("pub-1") is None  # "아직 안 봤다"로 남는다
    assert store.events == []


def test_관측이_있으면_보상과_통계가_함께_선다() -> None:
    store = _store()
    _observe(store)
    report = run_reward_batch(store=store, now=NOW, goal_ref=GOAL)

    assert (report.computed, report.insufficient) == (1, 0)
    saved = store.read_reward("pub-1")
    assert saved is not None and saved[0] is not None
    assert saved[1] == formula_version(GOAL)
    stats = store.read_topic_stats()
    assert (stats[0].trials, stats[0].reward_sum) == (1, saved[0])


def test_표본_부족은_NULL로_남고_학습에서_빠진다() -> None:
    """폴링은 됐지만 전 지표가 결측 — 그 사실이 reward=NULL로 보존된다(FR-L2)."""
    store = _store()
    _observe(store, reach=None, shares=None, saved=None, likes=None, views=None)
    report = run_reward_batch(store=store, now=NOW, goal_ref=GOAL)

    assert (report.insufficient, report.computed) == (1, 0)
    assert store.read_reward("pub-1") == (None, formula_version(GOAL))
    assert store.read_topic_stats() == ()  # NULL 행은 trials를 만들지 않는다


def test_재계산이_trials를_부풀리지_않는다() -> None:
    """배치는 스케줄러가 반복해서 부른다 — 두 번 돌았다고 성과가 두 배가 되면 안 된다."""
    store = _store()
    _observe(store)
    first = run_reward_batch(store=store, now=NOW, goal_ref=GOAL)
    second = run_reward_batch(store=store, now=NOW, goal_ref=GOAL)
    forced = run_reward_batch(store=store, now=NOW, goal_ref=GOAL, recompute=True)

    assert (second.absorbed, second.computed) == (1, 0)  # 같은 산식이면 건너뛴다
    assert forced.computed == 1  # 강제 재계산은 실제로 다시 쓴다
    stats = store.read_topic_stats()
    assert stats[0].trials == 1
    assert stats[0].reward_sum == pytest.approx(store.read_topic_stats()[0].reward_sum)
    assert first.computed == 1


def test_산식_버전이_오르면_다시_계산한다() -> None:
    """사전등록으로 계수가 바뀌는 날, 옛 버전으로 적힌 값이 그대로 남으면 두 산식이 섞인다."""
    store = _store()
    _observe(store)
    store.save_reward(publication_id="pub-1", reward_value=0.1, formula_version="v0-old+" + GOAL)

    report = run_reward_batch(store=store, now=NOW, goal_ref=GOAL)

    assert report.computed == 1
    saved = store.read_reward("pub-1")
    assert saved is not None and saved[1] == formula_version(GOAL)
    assert store.read_topic_stats()[0].trials == 1  # 갱신이지 추가가 아니다


def test_지평_밖_발행분은_훑지_않는다() -> None:
    """지평이 없으면 원장이 자란 뒤 published_items가 늘 옛 건만 돌려준다."""
    store = _store(published_h_ago=24 * 40)
    _observe(store)
    report = run_reward_batch(store=store, now=NOW, goal_ref=GOAL, lookback_days=30)
    assert report.items == 0
    assert store.read_reward("pub-1") is None


def test_배치_흔적은_한_줄만_남는다() -> None:
    """건마다 남기면 "표본 부족"이 원장에 두 벌로 생긴다 — 정본은 reward 테이블이다."""
    store = _store()
    _observe(store)
    run_reward_batch(store=store, now=NOW, goal_ref=GOAL)

    assert len(store.events) == 1
    event = store.events[0]
    assert event["kind"] == "notice"
    assert event["payload"] == {
        "reason": "reward_batch",
        "formula_version": formula_version(GOAL),
        "computed": 1,
        "insufficient": 0,
        "pending": 0,
    }


def test_할_일이_없으면_원장을_건드리지_않는다() -> None:
    store = _store()
    report = run_reward_batch(store=store, now=NOW, goal_ref=GOAL)
    assert (report.items, report.pending) == (1, 1)
    assert store.events == []
