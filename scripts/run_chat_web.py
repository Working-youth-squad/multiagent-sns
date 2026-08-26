"""키워드 챗봇 웹 서버 기동. uv run python scripts/run_chat_web.py

전제:
  1. docker compose up -d postgres (+ python -m sns.db.migrate — 마이그 004 필요)
  2. LLM 키 — **필수**(앱 본체가 대화다). env SNS_MODEL_PROVIDER로 고른다:
     · gemini(기본) → GEMINI_API_KEY   · openai → OPENAI_API_KEY
     모델은 GEMINI_MODEL / OPENAI_MODEL로 덮어쓴다.
  3. env DATABASE_URL — (선택) 기본 postgresql://sns:sns@localhost:5432/sns
  4. env CHAT_WEB_HOST/PORT — (선택) 기본 127.0.0.1:8003
  5. env CARD_FONT — (선택) 한글 TTF 경로. 미지정 시 자동 탐색
  6. env APPROVE_WEB_BASE — (선택) 승인 화면 주소. 기본 http://127.0.0.1:8001

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

from sns.agents.models import make_model, required_key_env, resolve_model_name, resolve_provider
from sns.agents.topic import TopicResult
from sns.chat.drafts import DraftItem, SeedOutcome, seed_done_message, seed_done_payload
from sns.chat.store import PgChatStore
from sns.quality.gate import QualityReport, check_card
from sns.render.card.media import CardRenderMedia
from sns.render.card.spec import parse_card_spec
from sns.research.trends import default_service
from sns.runner.cycle import AssessQuality, CycleTarget, TargetResult, run_cycle
from sns.runner.store import PgCycleStore
from sns.tools.contracts import ContentFormat, MediaAsset, MediaKind
from sns.tools.fakes import FakeReadStats
from sns.web.chat.app import LoadMediaFn, StartCycleFn, create_app

ENV_FILE = Path(__file__).parent.parent / ".env"
DEFAULT_DSN = "postgresql://sns:sns@localhost:5432/sns"
DEFAULT_APPROVE_BASE = "http://127.0.0.1:8001"
_MEDIA_TYPES = {"image": "image/png", "video": "video/mp4"}
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


def _draft_item(conn: psycopg.Connection, target: TargetResult) -> DraftItem:
    """TargetResult + 원장 조회 → 대화에 실을 1건. 조회 실패가 결과 보고를 막지 않는다."""
    row = conn.execute(
        "SELECT platform, handle FROM channel WHERE id = %s", (target.channel_id,)
    ).fetchone()
    label = f"{row[0]} @{row[1]}" if row else target.channel_id[:8]

    body = ""
    content_status = None
    if target.content_item_id:
        found = conn.execute(
            "SELECT body, status FROM content_item WHERE id = %s", (target.content_item_id,)
        ).fetchone()
        if found:
            body = str(found[0]) if found[0] else ""
            content_status = str(found[1])

    quality = None
    if target.media_asset_id:
        found = conn.execute(
            "SELECT quality_status FROM media_asset WHERE id = %s", (target.media_asset_id,)
        ).fetchone()
        quality = str(found[0]) if found else None

    return DraftItem(
        channel_label=label,
        outcome=target.outcome,
        content_item_id=target.content_item_id,
        body=body,
        media_asset_id=target.media_asset_id,
        content_status=content_status,
        quality_status=quality,
        error=target.error,
    )


def make_load_media_fn(dsn: str) -> LoadMediaFn:
    """media_asset_id → (바이트, MIME). id로만 받아 임의 경로 읽기를 막는다."""
    store = DirMediaStore(OUT)

    def load(asset_id: str) -> tuple[bytes, str] | None:
        with psycopg.connect(dsn, connect_timeout=10, autocommit=True) as conn:
            row = conn.execute(
                "SELECT storage_url, kind FROM media_asset WHERE id = %s", (asset_id,)
            ).fetchone()
        if row is None:
            return None
        return store.get(str(row[0])), _MEDIA_TYPES.get(str(row[1]), "application/octet-stream")

    return load


def make_start_cycle_fn(dsn: str, chat_store: PgChatStore, *, approve_base: str) -> StartCycleFn:
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
                outcome = SeedOutcome(
                    cycle_id=result.cycle_id,
                    status=result.status,
                    topic_title=topic.title,
                    items=tuple(_draft_item(conn, t) for t in result.targets),
                )
                chat_store.append(
                    conversation_id,
                    role="system",
                    body=seed_done_message(outcome),
                    payload=seed_done_payload(outcome, approve_base=approve_base),
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
    try:
        provider = resolve_provider()
    except RuntimeError as exc:
        print(f"중단: {exc}")
        return 1
    key_env = required_key_env(provider)
    if not os.environ.get(key_env):
        print(f"중단: env {key_env} 없음 — 챗봇 본체가 LLM 대화입니다.")
        print("      gemini: https://aistudio.google.com/apikey (무료 티어)")
        print("      openai: https://platform.openai.com/api-keys")
        return 1
    print(f"모델 : {provider} / {resolve_model_name(provider)}")

    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    try:
        conn = psycopg.connect(dsn, connect_timeout=10, autocommit=True)
    except psycopg.OperationalError as exc:
        print(f"중단: PostgreSQL 연결 실패 — docker compose up -d postgres\n      {exc}")
        return 1

    store = PgChatStore(conn)
    approve_base = os.environ.get("APPROVE_WEB_BASE", DEFAULT_APPROVE_BASE)
    app = create_app(
        store,
        model=make_model(),
        start_cycle_fn=make_start_cycle_fn(dsn, store, approve_base=approve_base),
        load_media_fn=make_load_media_fn(dsn),
    )
    host = os.environ.get("CHAT_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("CHAT_WEB_PORT", "8003"))
    print(f"키워드 챗봇: http://{host}:{port}/")
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
