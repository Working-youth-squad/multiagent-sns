"""온보딩 인터뷰 웹 서버 기동. uv run python scripts/run_onboarding_web.py

전제:
  1. docker compose up -d postgres (+ python -m sns.db.migrate)
  2. env DATABASE_URL — (선택) 기본 postgresql://sns:sns@localhost:5432/sns
  3. env ONBOARD_WEB_HOST/PORT — (선택) 기본 127.0.0.1:8002
  4. (선택) GEMINI_API_KEY — 있으면 트렌드 추천·줄글 미세조정·캐릭터 생성이 켜진다.
     없으면 인터뷰·프로필 저장만 동작한다(협력자 셋 다 탈부착 가능).

단일 커넥션 전제(scripts/run_approve_web.py와 동일 규율).
"""

import os
import sys
from pathlib import Path

import psycopg
import uvicorn
from dotenv import load_dotenv

from sns.web.onboarding.app import create_app

ENV_FILE = Path(__file__).parent.parent / ".env"
DEFAULT_DSN = "postgresql://sns:sns@localhost:5432/sns"
CHAR_DIR = Path(__file__).parent / "out" / "characters"


class DirMediaStore:
    """캐릭터 PNG를 디스크에 떨어뜨린다(scripts/e2e_cycle.py와 동형) — 재기동에도 남게."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, *, checksum: str, kind: str, ext: str) -> str:
        path = self.root / f"{kind}-{checksum[:16]}.{ext}"
        path.write_bytes(data)
        return path.resolve().as_uri()

    def get(self, url: str) -> bytes:
        from urllib.parse import urlparse
        from urllib.request import url2pathname

        return Path(url2pathname(urlparse(url).path)).read_bytes()


def main() -> int:
    load_dotenv(ENV_FILE, override=False)
    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    try:
        conn = psycopg.connect(dsn, connect_timeout=10, autocommit=True)
    except psycopg.OperationalError as exc:
        print(f"중단: PostgreSQL 연결 실패 — docker compose up -d postgres\n      {exc}")
        return 1

    from sns.agents.models import make_model
    from sns.onboarding.character import make_character_fn
    from sns.onboarding.recommend import make_recommend_fn, make_refine_fn
    from sns.onboarding.store import PgOnboardingStore

    recommend_fn = None
    refine_fn = None
    if os.environ.get("GEMINI_API_KEY"):
        model = make_model()
        recommend_fn = make_recommend_fn(model)
        refine_fn = make_refine_fn(model)
    # 캐릭터 생성은 항상 배선 — 키·할당량이 없으면 ImageGenerationError가 나고
    # 웹 앱이 "캐릭터 없음"으로 온보딩을 계속한다(비용 통제는 character.py 몫).
    ensure_character_fn = make_character_fn(DirMediaStore(CHAR_DIR))

    app = create_app(
        PgOnboardingStore(conn),
        recommend_fn=recommend_fn,
        refine_fn=refine_fn,
        ensure_character_fn=ensure_character_fn,
    )
    host = os.environ.get("ONBOARD_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("ONBOARD_WEB_PORT", "8002"))
    print(f"온보딩 인터뷰: http://{host}:{port}/")
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
