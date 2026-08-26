"""키워드 챗봇 웹 서버 기동. uv run python scripts/run_chat_web.py

전제:
  1. docker compose up -d postgres (+ python -m sns.db.migrate — 마이그 004 필요)
  2. env GEMINI_API_KEY — **필수**. 이 앱의 본체가 LLM 대화다.
  3. env DATABASE_URL — (선택) 기본 postgresql://sns:sns@localhost:5432/sns
  4. env CHAT_WEB_HOST/PORT — (선택) 기본 127.0.0.1:8003
  5. env CARD_FONT — (선택) 한글 TTF 경로. 미지정 시 자동 탐색

단일 커넥션 전제(scripts/run_approve_web.py와 동일 규율).

**시드 발행은 hybrid 채널에서만 돈다**(FR-W5). 확정된 주제는 초안·렌더·게이트를 거쳐
승인 대기로 들어가고, 사람이 승인 화면(scripts/run_approve_web.py, :8001)에서 확인한 뒤
발행된다 — 챗봇이 곧바로 세상에 올리지 않는다.

사이클은 **별도 스레드**에서 돈다. 폼 POST 전체 새로고침 방식이라 동기로 완주시키면
브라우저가 분 단위로 멈춘다. 진행·결과는 대화에 system 메시지로 붙으므로 사용자는
새로고침으로 확인한다.
"""

import os
import sys
import threading
from collections.abc import Mapping
from pathlib import Path

import psycopg
import uvicorn
from dotenv import load_dotenv

from sns.agents.models import make_model
from sns.agents.topic import TopicResult
from sns.chat.store import PgChatStore
from sns.quality.gate import QualityReport, check_card
from sns.render.card.media import CardRenderMedia
from sns.render.card.spec import parse_card_spec
from sns.research.trends import default_service
from sns.runner.cycle import AssessQuality, CycleTarget, run_cycle
from sns.runner.store import PgCycleStore
from sns.tools.contracts import ContentFormat, MediaAsset, MediaKind
from sns.tools.fakes import FakeReadStats
from sns.web.chat.app import StartCycleFn, create_app

ENV_FILE = Path(__file__).parent.parent / ".env"
DEFAULT_DSN = "postgresql://sns:sns@localhost:5432/sns"
OUT = Path(__file__).parent / "out"
FONT_CANDIDATES = (
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
)


class DirMediaStore:
    """렌더 바이트를 디스크에 (scripts/e2e_cycle.py와 동형)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, *, checksum: str, kind: MediaKind, ext: str) -> str:
        path = self.root / f"{kind}-{checksum[:16]}.{ext}"
        path.write_bytes(data)
        return path.resolve().as_uri()

    def get(self, url: str) -> bytes:
        from urllib.parse import urlparse
        from urllib.request import url2pathname

        return Path(url2pathname(urlparse(url).path)).read_bytes()


def find_font() -> str | None:
    env = os.environ.get("CARD_FONT")
    if env:
        return env
    return next((c for c in FONT_CANDIDATES if Path(c).exists()), None)


def make_gate(renderer: CardRenderMedia) -> AssessQuality:
    def assess(
        *, media_spec: Mapping[str, object], media: MediaAsset, content_format: ContentFormat
    ) -> QualityReport:
        spec = parse_card_spec(media_spec)
        return check_card(spec, renderer.render(media_spec))

    return assess


def hybrid_targets(conn: psycopg.Connection) -> list[CycleTarget]:
    """시드 발행 대상 = hybrid 채널 전부(FR-W5: 수동 시드는 hybrid에서만)."""
    rows = conn.execute(
        "SELECT id, platform FROM channel WHERE mode = 'hybrid' AND status = 'active' "
        "ORDER BY created_at"
    ).fetchall()
    return [
        CycleTarget(
            channel_id=str(r[0]),
            platform=r[1],
            # 챗봇 시드는 피드 카드로 고정한다 — 포맷 선택까지 대화에 얹으면 통제변수가
            # 하나 더 늘어난다(01). 포맷 확장은 별도 결정 사항.
            content_format="feed_image",
            mode="hybrid",
        )
        for r in rows
    ]


def make_start_cycle_fn(dsn: str, chat_store: PgChatStore) -> StartCycleFn:
    """(conversation_id, topic) → 백그라운드 사이클. 즉시 반환한다."""
    font = find_font()

    def worker(conversation_id: str, topic: TopicResult) -> None:
        # 스레드마다 자기 커넥션을 연다 — psycopg 커넥션은 스레드 간 공유 대상이 아니고,
        # 웹 요청이 쓰는 커넥션을 분 단위 작업이 붙들면 화면이 멈춘다.
        try:
            with psycopg.connect(dsn, connect_timeout=10, autocommit=True) as conn:
                targets = hybrid_targets(conn)
                if not targets:
                    chat_store.append(
                        conversation_id,
                        role="system",
                        body=(
                            "초안을 만들 채널이 없습니다 — hybrid 모드 채널이 필요합니다"
                            " (온보딩 :8002에서 만들 수 있습니다)."
                        ),
                        payload={"kind": "seed_no_target"},
                    )
                    return
                renderer = CardRenderMedia(DirMediaStore(OUT), font_path=font)
                result = run_cycle(
                    PgCycleStore(conn),
                    goal_ref="engagement_depth",
                    targets=targets,
                    model=make_model(),
                    # seed_topic이 있으면 주제 선택 경로를 타지 않아 이 둘은 호출되지
                    # 않는다. 계약상 필수라 넘길 뿐이다(run_cycle docstring).
                    research_trends=default_service(),
                    read_stats=FakeReadStats(),
                    render_media=renderer,
                    assess_quality=make_gate(renderer),
                    seed_topic=topic,
                )
                prepared = len(result.prepared)
                if prepared:
                    body = (
                        f"초안 {prepared}건을 만들었습니다. "
                        "승인 화면(:8001)에서 확인하고 승인하면 발행됩니다."
                    )
                else:
                    reasons = "; ".join(t.error or t.outcome for t in result.targets) or "사유 없음"
                    body = f"초안을 만들지 못했습니다 — {reasons}"
                chat_store.append(
                    conversation_id,
                    role="system",
                    body=body,
                    payload={
                        "kind": "seed_done",
                        "cycle_id": result.cycle_id,
                        "status": result.status,
                        "prepared": prepared,
                    },
                )
        except Exception as exc:  # 스레드에서 죽으면 사용자는 영영 모른다 — 대화에 남긴다
            chat_store.append(
                conversation_id,
                role="system",
                body=f"초안 제작이 실패했습니다: {exc}",
                payload={"kind": "seed_crashed", "error": str(exc)},
            )

    def start(conversation_id: str, topic: TopicResult) -> None:
        threading.Thread(target=worker, args=(conversation_id, topic), daemon=True).start()

    return start


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    load_dotenv(ENV_FILE, override=False)
    if not os.environ.get("GEMINI_API_KEY"):
        print("중단: env GEMINI_API_KEY 없음 — 챗봇 본체가 LLM 대화입니다.")
        print("      무료 키 발급: https://aistudio.google.com/apikey")
        return 1

    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    try:
        conn = psycopg.connect(dsn, connect_timeout=10, autocommit=True)
    except psycopg.OperationalError as exc:
        print(f"중단: PostgreSQL 연결 실패 — docker compose up -d postgres\n      {exc}")
        return 1

    store = PgChatStore(conn)
    app = create_app(
        store,
        model=make_model(),
        start_cycle_fn=make_start_cycle_fn(dsn, store),
    )
    host = os.environ.get("CHAT_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("CHAT_WEB_PORT", "8003"))
    print(f"키워드 챗봇: http://{host}:{port}/")
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
