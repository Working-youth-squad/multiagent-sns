"""E2E: 실 Analytics 폴링 → 스코어보드 → 실 Gemini Analyst 분석글 (FR-A1·L5).

전제:
  1. .secrets/client_secret.json (기존)
  2. env GEMINI_API_KEY — Gemini (aistudio.google.com/apikey)
  3. **기존 .secrets/token.json 삭제 필요** — analytics scope가 추가되어 재동의해야 함.
     (기존 토큰은 새 scope 없이도 valid로 로드되므로 자동 감지 불가)

실행: uv run python scripts/e2e_analyst.py
기대: 비공개·0뷰 영상 + 기준선 0건 → 전 신호 missing/no_verdict →
      insufficient_evidence=True, 분석글에 "판정 불가" — 정직 결측 경로의 실증.
"""

from pathlib import Path

from sns.adapters.youtube.auth import build_youtube_analytics, load_credentials
from sns.adapters.youtube.metrics import YouTubeMetrics
from sns.agents.analyst import AnalysisRejected, run_analysis
from sns.agents.models import make_model
from sns.tools.fakes import FakeReadStats, FakeWritePlaybook

SECRETS = Path(__file__).parent.parent / ".secrets"
POST_ID = "XoB6SuTMEvQ"  # 첫 자동 업로드 쇼츠


def main() -> None:
    token = SECRETS / "token.json"
    if token.exists():
        print(f"주의: {token} 이 이전 scope로 발급된 것이면 삭제 후 재실행 필요")

    print("1/3 OAuth (analytics scope 포함)…")
    creds = load_credentials(SECRETS / "client_secret.json", token)
    analytics = build_youtube_analytics(creds)

    print("2/3 실 지표 폴링…")
    poll = YouTubeMetrics(analytics)
    for value in poll("youtube", POST_ID, 0):
        print(f"    {value.metric_key}: {'결측' if value.missing else value.value}")

    print("3/3 Analyst 에이전트 (Gemini)…")
    try:
        result = run_analysis(
            make_model(),
            platform="youtube",
            post_ids=(POST_ID,),
            window_index=0,
            poll_metrics=poll,
            read_stats=FakeReadStats(),  # DB 없음 — 러너 연결은 후속
            write_playbook=FakeWritePlaybook(),
        )
    except AnalysisRejected as exc:
        print("--- 거부된 본문 ---")
        print(exc.body)
        raise SystemExit(f"검증기 거부: {exc.reasons}") from exc
    print(f"insufficient_evidence={result.insufficient_evidence}")
    print(f"playbook_written={result.playbook_written}")
    print("--- 분석글 ---")
    print(result.body)


if __name__ == "__main__":
    main()
