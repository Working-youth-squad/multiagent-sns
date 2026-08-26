"""지표 폴러 (FR-L1, M6) — 발행 원장을 훑어 기한이 된 창을 찍는다.

한 번 호출 = 한 번 훑기. 상주 루프가 아니라 스케줄러(cron·작업 스케줄러)가 부르는 배치다
— `scripts/run_metrics_poll.py`가 그 입구다. 상주로 만들면 폴링 간격이 코드에 박히고,
간격을 바꾸려 프로세스를 재시작해야 한다.

## 이 모듈이 지키는 것

- **정책은 스케줄러에**(`sns.learning.schedule`), **적재는 저장소에**(`sns.learning.stores`).
  여기 있는 것은 그 둘을 잇는 순서와 **실패를 어디서 멈출 것인가**뿐이다.
- **한 건의 실패가 훑기를 끊지 않는다.** 발행 라우터(`sns/publish/router.py`)가 미배선
  플랫폼 때문에 루프가 끊기던 것을 고친 것과 같은 이유다. 인스타 폴러가 아직 없다고
  유튜브 지표를 못 모으면 실험이 통째로 멈춘다.
- **없는 관측을 만들지 않는다.** 어댑터가 준 `missing`을 그대로 통과시키고(0으로 채우지
  않는다), 유예를 넘긴 창은 값을 지어내는 대신 `missed`로 남긴다.

## 왜 플랫폼별 dict인가

`PollMetrics`는 플랫폼을 인자로 받지만, 구현은 어댑터마다 따로다(YT=Analytics API,
IG=Graph API). 하나로 합친 라우터를 여기서 만들면 IG 폴러(IG-3)가 오는 날 이 파일을
고쳐야 한다. dict에 한 줄 넣으면 켜지게 둔다 — `sns/runner/wiring.py`와 같은 규율.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from sns.learning.schedule import DEFAULT_HORIZON_DAYS, WindowPlan, plan_windows
from sns.learning.stores import MetricStore, PublishedItem
from sns.tools.contracts import Platform, PollMetrics


@dataclass(frozen=True)
class PollReport:
    """한 번 훑은 결과 — 운영자가 콘솔에서 읽고, 테스트가 겨누는 요약."""

    items: int = 0  # 대상이 된 발행 건
    observed: int = 0  # 새로 적재된 관측
    absorbed: int = 0  # 이미 찍혀 있어 흡수된 창(재실행·경합)
    missed: int = 0  # 유예를 넘겨 포기한 창
    failed: int = 0  # 어댑터 오류로 못 찍은 창
    unrouted: int = 0  # 폴러가 없는 플랫폼의 발행 건

    def summary(self) -> str:
        return (
            f"대상 {self.items}건 · 적재 {self.observed} · 흡수 {self.absorbed} · "
            f"놓침 {self.missed} · 실패 {self.failed} · 미배선 {self.unrouted}"
        )


def poll_due_metrics(
    *,
    store: MetricStore,
    pollers: Mapping[Platform, PollMetrics],
    now: datetime,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    limit: int = 200,
) -> PollReport:
    """기한이 된 창을 전부 찍어 적재한다. `now`는 주입 — 결정론 테스트의 전제(NFR-2)."""
    since = now - timedelta(days=horizon_days)
    items = store.published_items(since=since, limit=limit)
    report = PollReport(items=len(items))
    for item in items:
        plan = plan_windows(
            published_at=item.published_at,
            now=now,
            observed=item.observed_windows,
            horizon_days=horizon_days,
        )
        report = _count_missed(plan, report)
        if not plan.due:
            continue
        poller = pollers.get(item.platform)
        if poller is None:
            # 미배선 플랫폼 — 건너뛰되 창은 그대로 남는다(어댑터가 붙으면 다음 훑기에
            # 잡힌다). 놓친 창과 같은 이유로 원장에는 적지 않는다: 어댑터가 없는 동안
            # 매 훑기가 같은 사실을 반복해 쓴다. 대신 보고서에 세어 CLI가 크게 알린다.
            report = _bump(report, unrouted=1)
            continue
        report = _poll_item(store, poller, item, plan, report, now=now)
    return report


def _poll_item(
    store: MetricStore,
    poller: PollMetrics,
    item: PublishedItem,
    plan: WindowPlan,
    report: PollReport,
    *,
    now: datetime,
) -> PollReport:
    for window_index in plan.due:
        try:
            values = poller(item.platform, item.external_post_id, window_index)
        except Exception as exc:  # noqa: BLE001 — 어댑터 오류는 이 건에서 멈춘다.
            # 계약상 PollMetrics에는 오류 채널이 없다(오류를 결측으로 뭉개면 NULL의
            # 의미가 오염되므로 raise가 정답이다 — YT 폴러 docstring). 그 raise를
            # 여기서 받아 이 건만 끊는다. 같은 건의 다음 창도 같은 이유로 실패할
            # 공산이 크므로 창 루프를 빠져나간다.
            store.log_event(
                cycle_id=None,
                kind="error",
                payload={
                    "reason": "metrics_poll_failed",
                    "publication_id": item.publication_id,
                    "platform": item.platform,
                    "window_index": window_index,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return _bump(report, failed=1)
        observation_id = store.save_observation(
            publication_id=item.publication_id,
            window_index=window_index,
            values=values,
            observed_at=now,
        )
        if observation_id is None:
            report = _bump(report, absorbed=1)
            continue
        # 폴링이 돌았다는 사실은 전 지표가 결측이어도 남는다 — 폴러가 안 돈 것과
        # API가 값을 안 준 것은 인사이트에서 다른 판정이다.
        store.log_event(
            cycle_id=None,
            kind="metric_polled",
            payload={
                "publication_id": item.publication_id,
                "platform": item.platform,
                "window_index": window_index,
                "keys": len(values),
                "missing": sum(1 for v in values if v.missing),
            },
        )
        report = _bump(report, observed=1)
    return report


def _count_missed(plan: WindowPlan, report: PollReport) -> PollReport:
    """놓친 창은 **세기만 한다** — `run_event`에 남기지 않는다.

    `plan_windows`가 결정론이라 한 번 놓친 창은 이후 모든 훑기에서 계속 놓친 창이다.
    매 실행마다 적으면 append-only 원장이 같은 사실로 채워진다. 대신 구멍은 **관측의
    부재**로 남고, 읽는 쪽이 같은 스케줄 함수로 되짚으면 어느 창이 비었는지 정확히
    안다 — 사실이 한 곳에만 있게 두는 편이 낫다.
    """
    return _bump(report, missed=len(plan.missed))


def _bump(
    report: PollReport,
    *,
    observed: int = 0,
    absorbed: int = 0,
    missed: int = 0,
    failed: int = 0,
    unrouted: int = 0,
) -> PollReport:
    return PollReport(
        items=report.items,
        observed=report.observed + observed,
        absorbed=report.absorbed + absorbed,
        missed=report.missed + missed,
        failed=report.failed + failed,
        unrouted=report.unrouted + unrouted,
    )
