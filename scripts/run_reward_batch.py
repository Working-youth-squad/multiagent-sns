"""보상 계산 1회 실행 (FR-L2, M6). uv run python scripts/run_reward_batch.py

대표 창(발행 후 72h)이 찍힌 발행 건을 훑어 goal별 산식으로 보상을 계산하고 `reward`에
적재한다. `topic_stats`는 저장소가 같은 트랜잭션에서 함께 갱신한다 — 이 스크립트는
통계를 건드리지 않는다(중복 집계 방지, `sns/learning/stores.py`).

**상주하지 않는다** — 폴러(`run_metrics_poll.py`)가 창을 찍은 뒤 하루 한 번쯤 부르면
되는 배치다. 폴링보다 자주 부를 이유가 없다: 대표 창의 관측은 한 번 찍히면 안 바뀐다.

전제:
  1. docker compose up -d postgres
  2. env DATABASE_URL — (선택) 기본 postgresql://sns:sns@localhost:5432/sns
  3. 폴러가 먼저 돌아 72h 창이 찍혀 있어야 한다. 아직이면 `대기`로 세어진다.

네트워크를 타지 않는다(관측은 이미 DB에 있다). 그래서 `--dry-run`은 값까지 전부
계산해 보여주고 저장만 하지 않는다 — 산식을 바꿀 때 무엇이 얼마나 달라지는지
운영 DB를 건드리지 않고 볼 수 있는 자리다.
"""

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from sns.goals import DEFAULT_GOAL_REF, GOAL_PRESETS
from sns.learning.reward import (
    DEFAULT_LOOKBACK_DAYS,
    RewardBatchReport,
    compute_reward,
    formula_version,
    observed_metrics,
    run_reward_batch,
)
from sns.learning.schedule import REWARD_WINDOW_INDEX
from sns.learning.stores import MetricStore, PgMetricStore

ENV_FILE = Path(__file__).parent.parent / ".env"
DEFAULT_DSN = "postgresql://sns:sns@localhost:5432/sns"


def preview(
    store: MetricStore, *, goal_ref: str, now: datetime, lookback_days: int, limit: int
) -> RewardBatchReport:
    """계산은 하되 저장하지 않는다 — `--dry-run`.

    배치와 같은 순서로 훑되 `save_reward`만 부르지 않는다. 갈라진 로직이 아니라
    **같은 산식**을 부르는 것이 요점이다: dry-run이 보여준 값과 실행이 적는 값이
    다르면 dry-run은 아무 쓸모가 없다.
    """
    items = store.published_items(since=now - timedelta(days=lookback_days), limit=limit)
    report = RewardBatchReport(items=len(items))
    computed = insufficient = pending = 0
    for item in items:
        if REWARD_WINDOW_INDEX not in item.observed_windows:
            pending += 1
            continue
        breakdown = compute_reward(
            goal_ref=goal_ref,
            platform=item.platform,
            metrics=observed_metrics(store, item.publication_id),
        )
        if breakdown.value is None:
            insufficient += 1
            shown = f"NULL ({breakdown.reason})"
        else:
            computed += 1
            shown = f"{breakdown.value:.4f}"
        print(
            f"  {item.platform:9} {item.external_post_id:16} → {shown:36} "
            f"근거 {list(breakdown.used)}"
            + (f" · 결측 {list(breakdown.missing)}" if breakdown.missing else "")
        )
    return RewardBatchReport(
        items=report.items, computed=computed, insufficient=insufficient, pending=pending
    )


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # 한글 콘솔(cp949)
    parser = argparse.ArgumentParser(description="발행분 보상 계산 1회")
    parser.add_argument("--dry-run", action="store_true", help="계산만 하고 적재하지 않음")
    parser.add_argument(
        "--goal",
        default=DEFAULT_GOAL_REF,
        choices=sorted(GOAL_PRESETS),
        help=f"보상 산식의 goal (기본 {DEFAULT_GOAL_REF})",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"며칠 전 발행분까지 되돌아볼지 (기본 {DEFAULT_LOOKBACK_DAYS})",
    )
    parser.add_argument("--limit", type=int, default=200, help="한 번에 훑을 발행 건 수")
    parser.add_argument(
        "--recompute",
        action="store_true",
        help="같은 산식 버전으로 이미 계산된 건도 다시 계산 (topic_stats는 차분만 반영)",
    )
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    now = datetime.now(UTC)
    version = formula_version(args.goal)

    with psycopg.connect(dsn, autocommit=True) as conn:
        store = PgMetricStore(conn)
        print(f"산식 {version} · 대표 창 {REWARD_WINDOW_INDEX}(72h) · 지평 {args.lookback_days}일")
        if args.dry_run:
            print(f"[dry-run] {now:%Y-%m-%d %H:%M} UTC — 적재하지 않는다")
            report = preview(
                store,
                goal_ref=args.goal,
                now=now,
                lookback_days=args.lookback_days,
                limit=args.limit,
            )
        else:
            report = run_reward_batch(
                store=store,
                now=now,
                goal_ref=args.goal,
                lookback_days=args.lookback_days,
                limit=args.limit,
                recompute=args.recompute,
            )

    print(report.summary())
    if report.pending:
        # 폴러가 안 돌았거나 아직 72h가 안 됐다. 전자면 지표가 영영 안 모인다.
        print(f"ℹ️  대표 창 미관측 {report.pending}건 — 폴러(run_metrics_poll.py)를 기다린다")
    if report.insufficient:
        print(
            f"⚠️  표본 부족 {report.insufficient}건 — reward=NULL로 학습에서 빠진다 "
            "(어느 지표가 없었는지는 --dry-run이 건별로 보여준다)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
