"""분석글 1회 작성 (FR-L4·L5, M6). uv run python scripts/run_analysis_note.py

저장된 관측을 표본으로 Analyst 에이전트를 돌려, **검증기를 통과한 글만**
`analysis_note`에 적재하고 지침을 `playbook`에 남긴다. 폴러와 마찬가지로 상주하지
않는다 — 관측이 쌓인 뒤 스케줄러가 부르는 배치다.

    scripts/run_metrics_poll.py   ← 먼저 (관측이 없으면 여기는 할 일이 없다)
    scripts/run_analysis_note.py  ← 여기

전제:
  1. docker compose up -d postgres
  2. env DATABASE_URL — (선택) 기본 postgresql://sns:sns@localhost:5432/sns
  3. env GEMINI_API_KEY (또는 SNS_MODEL_PROVIDER=openai + OPENAI_API_KEY)

`--dry-run`은 **LLM을 부르지 않는다**: 어떤 게시물이 대상이 되고 기준선이 몇 건인지,
판정이 나올 표본인지만 보여준다. 무료 티어 쿼터가 하루 20건이라(모델별) 표본이 틀린 채
호출을 태우면 그날 작업이 멈춘다 — 확인이 먼저다.

**분석은 API를 다시 때리지 않는다.** 지표는 폴러가 적재해 둔 값을 읽는다
([sns.learning.report] 모듈 docstring). 여기에 실 어댑터를 배선하는 손잡이는 없다.
"""

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from sns.agents.models import make_model, required_key_env, resolve_model_name
from sns.learning.observations import StoredMetrics
from sns.learning.report import (
    DEFAULT_BASELINE_LIMIT,
    DEFAULT_LEDGER_LIMIT,
    select_sample,
    write_analysis_notes,
)
from sns.learning.schedule import REWARD_WINDOW_INDEX
from sns.learning.stores import MetricStore, PgMetricStore
from sns.signals.scoreboard import MIN_BASELINE_N
from sns.tools.contracts import Platform

ENV_FILE = Path(__file__).parent.parent / ".env"
DEFAULT_DSN = "postgresql://sns:sns@localhost:5432/sns"
PLATFORMS: tuple[Platform, ...] = ("youtube", "instagram")


def preview(
    store: MetricStore,
    *,
    platforms: tuple[Platform, ...],
    window_index: int,
    baseline_limit: int,
    since: datetime | None,
    ledger_limit: int,
) -> int:
    """표본만 출력 — `--dry-run`. LLM도 원장 쓰기도 없다."""
    stored = StoredMetrics(store, since=since, limit=ledger_limit)
    found = 0
    for platform in platforms:
        sample = select_sample(
            stored, platform, window_index=window_index, baseline_limit=baseline_limit
        )
        if sample is None:
            print(f"  {platform:9} 창 {window_index}이 찍힌 게시물이 없다 — 건너뜀")
            continue
        found += 1
        verdict = (
            "판정 가능"
            if sample.verdict_available
            else f"판정 불가(기준선 {MIN_BASELINE_N}건 필요)"
        )
        print(
            f"  {platform:9} 대상 {sample.target_post_id} · "
            f"기준선 {len(sample.baseline_post_ids)}건 · {verdict}"
        )
    return found


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # 한글 콘솔(cp949)
    parser = argparse.ArgumentParser(description="저장된 관측으로 분석글 1회 작성")
    parser.add_argument(
        "--platform",
        action="append",
        choices=PLATFORMS,
        help="분석할 플랫폼 (여러 번 지정 가능, 기본 전부)",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=REWARD_WINDOW_INDEX,
        help=f"관측 창 인덱스 (기본 {REWARD_WINDOW_INDEX}=72h — reward와 같은 창)",
    )
    parser.add_argument(
        "--baseline-limit",
        type=int,
        default=DEFAULT_BASELINE_LIMIT,
        help=f"기준선에 넣을 최근 게시물 수 (기본 {DEFAULT_BASELINE_LIMIT})",
    )
    parser.add_argument(
        "--since-days", type=int, default=None, help="이 기간 안의 발행분만 표본으로 (기본 전체)"
    )
    parser.add_argument("--ledger-limit", type=int, default=DEFAULT_LEDGER_LIMIT)
    parser.add_argument("--cycle-id", default=None, help="사이클 안에서 돌 때만 (기본 없음)")
    parser.add_argument("--dry-run", action="store_true", help="표본만 출력, LLM 호출 없음")
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    platforms: tuple[Platform, ...] = tuple(args.platform) if args.platform else PLATFORMS
    since = None if args.since_days is None else datetime.now(UTC) - timedelta(days=args.since_days)

    with psycopg.connect(dsn, autocommit=True) as conn:
        store = PgMetricStore(conn)
        if args.dry_run:
            print(f"[dry-run] 창 {args.window} · 플랫폼 {list(platforms)}")
            found = preview(
                store,
                platforms=platforms,
                window_index=args.window,
                baseline_limit=args.baseline_limit,
                since=since,
                ledger_limit=args.ledger_limit,
            )
            print(f"→ 분석 대상 {found}건" + ("" if found else " — 먼저 run_metrics_poll"))
            return 0

        # 모델 준비는 DB를 연 뒤·에이전트를 부르기 전에 — 키가 없으면 표본을 훑기 전에
        # 알려 주는 편이 낫다.
        key_env = required_key_env()
        if not os.environ.get(key_env):
            print(f"env {key_env} 누락 — --dry-run으로 표본만 확인할 수 있다")
            return 1
        print(f"모델 {resolve_model_name()} · 창 {args.window} · 플랫폼 {list(platforms)}")
        reports = write_analysis_notes(
            make_model(),
            store,
            platforms=platforms,
            window_index=args.window,
            cycle_id=args.cycle_id,
            baseline_limit=args.baseline_limit,
            since=since,
            ledger_limit=args.ledger_limit,
        )

    for report in reports:
        print(f"  {report.summary()}")
    rejected = [r for r in reports if r.rejected_reasons]
    if rejected:
        # 조용히 넘어가면 "분석글이 왜 안 늘지"의 답이 원장 안에만 남는다.
        print(f"⚠️  검증기 거부 {len(rejected)}건 — run_event(kind='error')에 본문 앞머리가 있다")
    failed = [r for r in reports if r.error]
    if failed:
        print(f"⚠️  실패 {len(failed)}건")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
