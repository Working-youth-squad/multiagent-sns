"""통합 웹 서버 — 온보딩 + 승인을 한 포트(:8000)에 마운트한다.

    uv run python scripts/run_web.py --topic-major 개발

경로: `/`(온보딩: 인터뷰·채널·새 포스트) + `/queue`(승인 대기열).
챗봇(:8003)은 **일부러 합치지 않는다** — run_chat_web.py는 LLM 키가 없으면
기동을 거부하는데, 그 조건을 여기에 물리면 키 하나 없을 때 웹 전체가
내려간다. 챗봇만 별도 프로세스로 격리한다.

전제는 두 단독 스크립트와 같다: postgres + DATABASE_URL, --topic-major(재렌더
컷 검증 규칙). env WEB_HOST/WEB_PORT(기본 127.0.0.1:8000).
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path(__file__).parent.parent / ".env"
load_dotenv(ENV_FILE, override=False)

# sns.web.layout은 import 시점에 네비 URL을 env에서 읽는다 — sns를 당기는
# 아래 import들보다 먼저 통합 토폴로지의 기본값을 고정한다(.env가 이미
# 정했다면 setdefault는 조용히 물러난다).
_HOST = os.environ.get("WEB_HOST", "127.0.0.1")
_PORT = int(os.environ.get("WEB_PORT", "8000"))
_BASE = f"http://{_HOST}:{_PORT}"
os.environ.setdefault("ONBOARD_WEB_BASE", _BASE)
os.environ.setdefault("APPROVE_WEB_BASE", f"{_BASE}/queue")
os.environ.setdefault("APPROVE_URL_PREFIX", "/queue")

import psycopg  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
import run_approve_web  # noqa: E402
import run_onboarding_web  # noqa: E402

DEFAULT_DSN = "postgresql://sns:sns@localhost:5432/sns"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 실행 파일 경로")
    parser.add_argument("--font", default=None, help="한글 TTF 경로 (미지정 시 자동 탐색)")
    parser.add_argument(
        "--topic-major", required=True, help="재렌더할 채널의 주제 대분류 (예: 개발, 요리)"
    )
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    try:
        # 앱마다 커넥션 1개 — 단독 실행 때와 같은 저트래픽 규율. 한 커넥션을
        # 공유하면 스레드풀 핸들러끼리 psycopg 커넥션을 겹쳐 쓰게 된다.
        conn_onboard = psycopg.connect(dsn, connect_timeout=10, autocommit=True)
        conn_approve = psycopg.connect(dsn, connect_timeout=10, autocommit=True)
    except psycopg.OperationalError as exc:
        print(f"중단: PostgreSQL 연결 실패 — docker compose up -d postgres\n      {exc}")
        return 1

    app = FastAPI(title="multiagent-sns web")
    app.mount(
        "/queue",
        run_approve_web.build_app(
            conn_approve, ffmpeg=args.ffmpeg, font=args.font, topic_major=args.topic_major
        ),
    )
    # 온보딩이 루트를 갖는다(인터뷰·채널·새 포스트) — 마지막에 마운트해야
    # /queue가 먼저 매칭된다.
    app.mount("/", run_onboarding_web.build_app(conn_onboard, dsn))

    print(f"통합 웹: {_BASE}/  (대기열: {_BASE}/queue, 챗봇은 별도 :8003)")
    uvicorn.run(app, host=_HOST, port=_PORT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
