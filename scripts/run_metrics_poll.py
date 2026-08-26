"""지표 폴링 1회 실행 (FR-L1, M6). uv run python scripts/run_metrics_poll.py

발행 원장을 훑어 기한이 된 관측 창(6h·24h·72h·이후 일 1회)을 찍어 적재한다.
**상주하지 않는다** — 작업 스케줄러/cron이 시간마다 부르는 배치다.

전제:
  1. docker compose up -d postgres
  2. env DATABASE_URL — (선택) 기본 postgresql://sns:sns@localhost:5432/sns
  3. 유튜브 폴링: `.secrets/client_secret.json` + `token.json`
     (analytics scope 포함 — 없으면 브라우저 동의가 뜬다)
  4. 인스타 폴링: env IG_ACCESS_TOKEN (IG 비즈니스 계정 토큰, insights 권한).
     없으면 IG 발행분은 `미배선`으로 세어지고 창은 그대로 남는다 — 토큰이 생기면
     다음 훑기에 잡힌다.

`--dry-run`은 DB를 읽되 쓰지 않는다: 무엇이 찍힐지만 보여준다. 실 계정 폴링 전에
스케줄이 의도대로 도는지 확인하는 자리다.
"""

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from sns.learning.poller import PollReport, poll_due_metrics
from sns.learning.schedule import DEFAULT_HORIZON_DAYS, plan_windows
from sns.learning.stores import MetricStore, PgMetricStore
from sns.tools.contracts import Platform, PollMetrics

ENV_FILE = Path(__file__).parent.parent / ".env"
SECRETS = Path(__file__).parent.parent / ".secrets"
DEFAULT_DSN = "postgresql://sns:sns@localhost:5432/sns"
ENV_IG_TOKEN = "IG_ACCESS_TOKEN"


def build_pollers(*, youtube: bool, instagram: bool) -> dict[Platform, PollMetrics]:
    """플랫폼 → 폴러. 없는 플랫폼은 넣지 않는다(폴러가 미배선을 스스로 센다).

    **토큰이 없으면 붙이지 않는다.** 빈 토큰으로 붙이면 발행분마다 401이 나서
    `run_event(kind='error')`가 쌓이고, 정작 "폴러가 없다"는 사실은 안 보인다 —
    미배선(unrouted)으로 세어지는 편이 정직하다.
    """
    pollers: dict[Platform, PollMetrics] = {}
    if youtube:
        from sns.adapters.youtube.auth import build_youtube_analytics, load_credentials
        from sns.adapters.youtube.metrics import YouTubeMetrics

        creds = load_credentials(SECRETS / "client_secret.json", SECRETS / "token.json")
        pollers["youtube"] = YouTubeMetrics(build_youtube_analytics(creds))
    if instagram:
        from sns.adapters.instagram.metrics import INSIGHTS_API_VERSION, InstagramMetrics
        from sns.adapters.instagram.publisher import UrllibGraphHttp

        token = os.environ.get(ENV_IG_TOKEN, "")
        if token:
            pollers["instagram"] = InstagramMetrics(
                UrllibGraphHttp(version=INSIGHTS_API_VERSION), access_token=token
            )
        else:
            print(f"인스타 폴러 생략 — {ENV_IG_TOKEN} 없음 (IG 발행분은 미배선으로 남는다)")
    return pollers


def preview(store: MetricStore, *, now: datetime, horizon_days: int) -> PollReport:
    """찍지 않고 계획만 출력 — `--dry-run`."""
    items = store.published_items(since=now - timedelta(days=horizon_days))
    due_total = missed_total = 0
    for item in items:
        plan = plan_windows(
            published_at=item.published_at,
            now=now,
            observed=item.observed_windows,
            horizon_days=horizon_days,
        )
        due_total += len(plan.due)
        missed_total += len(plan.missed)
        age_h = (now - item.published_at).total_seconds() / 3600
        print(
            f"  {item.platform:9} {item.external_post_id:16} 발행 {age_h:6.1f}h 전 · "
            f"찍힌 창 {list(item.observed_windows)} · 이번 {list(plan.due)}"
            + (f" · 놓침 {list(plan.missed)}" if plan.missed else "")
        )
    return PollReport(items=len(items), observed=due_total, missed=missed_total)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # 한글 콘솔(cp949)
    parser = argparse.ArgumentParser(description="발행분 지표 폴링 1회")
    parser.add_argument("--dry-run", action="store_true", help="계획만 출력, 적재하지 않음")
    parser.add_argument("--no-youtube", action="store_true", help="유튜브 폴러를 붙이지 않음")
    parser.add_argument("--no-instagram", action="store_true", help="인스타 폴러를 붙이지 않음")
    parser.add_argument(
        "--horizon-days",
        type=int,
        default=DEFAULT_HORIZON_DAYS,
        help=f"발행 후 며칠까지 폴링할지 (기본 {DEFAULT_HORIZON_DAYS})",
    )
    parser.add_argument("--limit", type=int, default=200, help="한 번에 훑을 발행 건 수")
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    now = datetime.now(UTC)

    with psycopg.connect(dsn, autocommit=True) as conn:
        store = PgMetricStore(conn)
        if args.dry_run:
            print(f"[dry-run] {now:%Y-%m-%d %H:%M} UTC · 지평 {args.horizon_days}일")
            report = preview(store, now=now, horizon_days=args.horizon_days)
            print(f"→ 대상 {report.items}건 · 찍을 창 {report.observed} · 놓친 창 {report.missed}")
            return 0

        pollers = build_pollers(youtube=not args.no_youtube, instagram=not args.no_instagram)
        if not pollers:
            print("폴러가 하나도 없다 — 아무것도 하지 않는다 (--no-youtube? 토큰 부재?)")
            return 1
        report = poll_due_metrics(
            store=store,
            pollers=pollers,
            now=now,
            horizon_days=args.horizon_days,
            limit=args.limit,
        )

    print(report.summary())
    if report.unrouted:
        # 조용히 넘어가면 IG 지표가 영영 안 모이는 것을 아무도 모른다.
        print(f"⚠️  폴러 없는 플랫폼의 발행 {report.unrouted}건 — 해당 플랫폼 자격증명 확인")
    if report.failed:
        print(f"⚠️  어댑터 오류 {report.failed}건 — run_event(kind='error')에 사유가 있다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
