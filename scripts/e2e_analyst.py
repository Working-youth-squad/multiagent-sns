"""E2E: 저장된 관측 → 스코어보드 → 실 Gemini Analyst → 원장 착지 (FR-A1·L4·L5).

`run_analysis_note.py`와 같은 배선을 쓰되, **1건을 자세히** 보여 준다: 어떤 표본이
잡혔고, 무엇이 적재됐고, 거부됐다면 어떤 본문이 왜 잘렸는지. 운영 배치는 조용해야 하고
관통 검증은 시끄러워야 한다.

## 무엇이 바뀌었나 (M6)

예전 이 스크립트는 **실 Analytics 어댑터를 `poll_metrics`에 직접 물리고**
`FakeReadStats`/`FakeWritePlaybook`으로 돌았다("DB 없음 — 러너 연결은 후속"). 그래서
분석할 때마다 API를 다시 때렸고, 나온 글과 지침은 어디에도 남지 않았다. 이제 셋 다
실물이다:

  poll_metrics  → StoredMetrics (폴러가 적재한 값. 네트워크 0)
  read_stats    → PgMetricStore.read_topic_stats
  write_playbook→ PgMetricStore.save_playbook (검증 통과 후에만 흘러간다)

폴링은 이 스크립트의 일이 아니다 — `scripts/run_metrics_poll.py`가 한다. 폴링 경로가 두
벌이면 한쪽만 고쳐지는 날이 온다(관측을 읽는 조각을 하나로 모은 것과 같은 이유).

전제:
  1. docker compose up -d postgres
  2. **먼저** uv run python scripts/run_metrics_poll.py  (관측이 없으면 표본이 없다)
  3. env GEMINI_API_KEY — aistudio.google.com/apikey

실행: uv run python scripts/e2e_analyst.py [--platform youtube] [--window 2]
"""

import argparse
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from sns.agents.models import make_model, required_key_env, resolve_model_name
from sns.learning.observations import StoredMetrics
from sns.learning.report import select_sample, write_analysis_note
from sns.learning.schedule import REWARD_WINDOW_INDEX
from sns.learning.stores import PgMetricStore
from sns.tools.contracts import Platform

ENV_FILE = Path(__file__).parent.parent / ".env"
DEFAULT_DSN = "postgresql://sns:sns@localhost:5432/sns"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # 한글 콘솔(cp949)
    parser = argparse.ArgumentParser(description="분석글 관통 1건 (실 LLM · 실 DB)")
    parser.add_argument("--platform", default="youtube", choices=("youtube", "instagram"))
    parser.add_argument("--window", type=int, default=REWARD_WINDOW_INDEX)
    args = parser.parse_args()
    platform: Platform = args.platform

    load_dotenv(ENV_FILE)
    key_env = required_key_env()
    if not os.environ.get(key_env):
        return _fail(f"env {key_env} 누락")

    with psycopg.connect(os.environ.get("DATABASE_URL", DEFAULT_DSN), autocommit=True) as conn:
        store = PgMetricStore(conn)

        print("1/3 표본 선정 (저장된 관측)…")
        stored = StoredMetrics(store)
        sample = select_sample(stored, platform, window_index=args.window)
        if sample is None:
            return _fail(
                f"창 {args.window}이 찍힌 {platform} 게시물이 없다 — "
                "먼저 uv run python scripts/run_metrics_poll.py"
            )
        print(f"    대상 {sample.target_post_id} · 기준선 {len(sample.baseline_post_ids)}건")
        print(f"    판정 {'가능' if sample.verdict_available else '불가(기준선 부족)'}")
        for key, value in sorted(
            stored.metrics_of(platform, sample.target_post_id, args.window).items()
        ):
            print(f"    {key}: {'결측' if value is None else value}")

        print(f"2/3 Analyst 에이전트 ({resolve_model_name()})…")
        report = write_analysis_note(
            make_model(), store, platform=platform, window_index=args.window, stored=stored
        )

        print("3/3 착지 확인…")
        print(f"    {report.summary()}")
        if report.rejected_reasons or report.error:
            # 거부된 본문 앞머리는 원장에 남는다 — 여기서 그것을 꺼내 보여 준다.
            row = conn.execute(
                "SELECT payload FROM run_event WHERE kind = 'error' "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                print("--- 거부/실패 기록 (run_event) ---")
                for key, value in sorted(row[0].items()):
                    print(f"    {key}: {value}")
            return 1
        note = conn.execute(
            "SELECT body FROM analysis_note WHERE id = %s", (report.note_id,)
        ).fetchone()
        print("--- 분석글 (analysis_note) ---")
        print(note[0] if note else "(적재 확인 실패)")
    return 0


def _fail(message: str) -> int:
    print(f"중단: {message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
