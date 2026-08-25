"""E2E: 한 사이클 실물 관통 — 기획→제작→품질→적재→발행 (C1·C2·C3·C5·C6).

**단위테스트가 아니다.** 가짜를 최소로 줄이고 실물 부품으로 한 사이클을 굴린 뒤,
사람이 눈으로 볼 수 있는 산출물(PNG 파일 + DB 원장 덤프 + Discord 메시지)을 남긴다.

실물 / 가짜 경계:
  실물  C1 트렌드     — Google Trends KR RSS 실조회 (무인증)
  실물  C2 에이전트   — Gemini 실호출 (Topic 주제선정 → Content 본문·media_spec)
  실물  C3 렌더+게이트 — Pillow PNG 파일 저장 + check_card 실판정
  실물  C5 원장       — PostgreSQL 실적재 + 멱등 상태머신 실전이
  실물  C6 알림       — Discord 웹훅 실전송 (URL 있을 때)
  가짜  발행 어댑터   — FakePublish (IG/YT 실계정 없이 원장 전이만 확인)
  가짜  read_stats    — FakeReadStats (topic_stats 조회의 운영 구현 아직 없음)

전제:
  1. docker compose up -d postgres
  2. env GEMINI_API_KEY        — aistudio.google.com/apikey (무료 티어)
  3. env DISCORD_WEBHOOK_URL   — (선택) 채널 설정 → 연동 → 웹훅
  4. env DATABASE_URL          — (선택) 기본 postgresql://sns:sns@localhost:5432/sns
  5. env CARD_FONT             — (선택) 한글 TTF 경로. 미지정 시 자동 탐색

실행: uv run python scripts/e2e_cycle.py
"""

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from dotenv import load_dotenv

from sns.agents.models import make_model
from sns.notify.alerts import publish_success
from sns.notify.discord import discord_sender_from_env
from sns.notify.dispatch import PgAlertSink, dispatch_alert
from sns.publish.runner import run_pending_publications
from sns.quality.gate import QualityReport, check_card
from sns.render.card.media import CardRenderMedia
from sns.render.card.spec import parse_card_spec
from sns.research.trends import default_service
from sns.runner.cycle import AssessQuality, CycleTarget, run_cycle
from sns.runner.store import PgCycleStore
from sns.tools.contracts import ContentFormat, MediaAsset, MediaKind
from sns.tools.fakes import FakePublish, FakeReadStats

OUT = Path(__file__).parent / "out"
ENV_FILE = Path(__file__).parent.parent / ".env"
DEFAULT_DSN = "postgresql://sns:sns@localhost:5432/sns"
FONT_CANDIDATES = (
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
)


class DirMediaStore:
    """렌더 바이트를 디스크에 떨어뜨린다 — 사람이 파일을 열어볼 수 있게."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.saved: list[Path] = []
        root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, *, checksum: str, kind: MediaKind, ext: str) -> str:
        path = self.root / f"{kind}-{checksum[:16]}.{ext}"
        path.write_bytes(data)
        self.saved.append(path)
        return path.resolve().as_uri()

    def get(self, url: str) -> bytes:
        return Path(urlparse(url).path.lstrip("/")).read_bytes()


def find_font() -> str | None:
    env = os.environ.get("CARD_FONT")
    if env:
        return env
    return next((c for c in FONT_CANDIDATES if Path(c).exists()), None)


def ensure_channel(conn: psycopg.Connection, *, handle: str) -> str:
    """데모용 instagram 채널 1개 확보(있으면 재사용)."""
    row = conn.execute("SELECT id FROM channel WHERE handle = %s", (handle,)).fetchone()
    if row is None:
        row = conn.execute(
            "INSERT INTO channel (platform, handle) VALUES ('instagram', %s) RETURNING id",
            (handle,),
        ).fetchone()
    assert row is not None
    return str(row[0])


def make_gate(renderer: CardRenderMedia) -> AssessQuality:
    """품질 게이트를 AssessQuality로 조립 — 게이트가 CardRender 내부를 봐야 하므로 caller 몫."""

    def assess(
        *, media_spec: Mapping[str, object], media: MediaAsset, content_format: ContentFormat
    ) -> QualityReport:
        spec = parse_card_spec(media_spec)
        return check_card(spec, renderer.render(media_spec))

    return assess


def dump(conn: psycopg.Connection, title: str, sql: str) -> None:
    rows = conn.execute(sql).fetchall()
    print(f"\n  -- {title} ({len(rows)}행)")
    for r in rows:
        print("     " + " | ".join("NULL" if v is None else str(v)[:60] for v in r))


def main() -> int:
    # Windows 콘솔 기본 코드페이지(cp949)는 em대시·한글 일부를 못 찍어 UnicodeEncodeError로
    # 죽는다. 업로드가 끝난 뒤 성공 메시지 출력에서 터져 video_id를 잃은 적이 있다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    # .env 로딩은 로컬 편의용이라 진입점에서만 한다 — 라이브러리(sns/)는 절대 .env를
    # 읽지 않는다(운영은 플랫폼 시크릿 주입). 셸에 이미 있는 값이 .env보다 우선(override=False).
    loaded = load_dotenv(ENV_FILE, override=False)
    print(f".env : {'로드됨' if loaded else '없음 — cp .env.example .env'}")

    if not os.environ.get("GEMINI_API_KEY"):
        print("중단: env GEMINI_API_KEY 없음 — C2 에이전트는 실 LLM이 필요합니다.")
        print("      무료 키 발급: https://aistudio.google.com/apikey")
        return 1

    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    font = find_font()
    print(f"DSN  : {dsn}")
    print(f"폰트 : {font or '없음 — Pillow 내장(한글 깨짐)'}")

    try:
        conn = psycopg.connect(dsn, connect_timeout=10, autocommit=True)
    except psycopg.OperationalError as exc:
        print(f"중단: PostgreSQL 연결 실패 — docker compose up -d postgres\n      {exc}")
        return 1

    with conn:
        store = PgCycleStore(conn)
        media_store = DirMediaStore(OUT)
        renderer = CardRenderMedia(media_store, font_path=font)
        channel_id = ensure_channel(conn, handle="e2e-demo")

        print("\n[1/5] C1 실 트렌드 조회 (Google Trends KR RSS)")
        trends = default_service()
        digest = trends(limit=10)
        for r in digest.source_results:
            print(f"      {r.source}: ok={r.ok}, {len(r.items)}건")
            for item in r.items[:5]:
                print(f"        - {item}")

        print("\n[2/5] C2+C3+C5 사이클 구동 (Gemini 실호출 → 카드 렌더 → 원장 적재)")
        result = run_cycle(
            store,
            goal_ref="engagement_depth",
            targets=[
                CycleTarget(
                    channel_id=channel_id, platform="instagram", content_format="feed_image"
                )
            ],
            model=make_model(),
            research_trends=trends,
            read_stats=FakeReadStats(),
            render_media=renderer,
            assess_quality=make_gate(renderer),
        )
        print(f"      cycle={result.cycle_id} status={result.status}")
        for t in result.targets:
            print(f"      {t.channel_id[:8]}... -> {t.outcome} {t.error or ''}")
        for p in media_store.saved:
            print(f"      렌더 산출물: {p}")

        if result.status != "completed":
            print("\n사이클 실패 — run_event를 확인하세요.")
            dump(conn, "run_event", "SELECT kind, payload FROM run_event ORDER BY created_at")
            return 1

        print("\n[3/5] 적재 결과 확인 (실 DB)")
        dump(
            conn,
            "topic",
            "SELECT title, COALESCE(summary, ''), COALESCE(source, '') "
            "FROM topic ORDER BY created_at DESC LIMIT 3",
        )
        dump(
            conn,
            "content_item",
            "SELECT format, status, hook_pattern, LEFT(COALESCE(body, ''), 80) "
            "FROM content_item ORDER BY created_at DESC LIMIT 3",
        )
        dump(
            conn,
            "media_asset",
            "SELECT kind, quality_status, LEFT(checksum, 16), storage_url "
            "FROM media_asset ORDER BY created_at DESC LIMIT 3",
        )
        dump(conn, "publication (발행 전)", "SELECT status FROM publication")

        print("\n[4/5] C5 발행 러너 — pending 원장 종결 (FakePublish)")
        publish = FakePublish()
        outcomes = run_pending_publications(conn, publish)
        for o in outcomes:
            print(f"      {o}")
        dump(
            conn,
            "publication (발행 후)",
            "SELECT status, COALESCE(external_post_id, '-') FROM publication",
        )
        dump(
            conn,
            "publish_attempt",
            "SELECT state, COALESCE(container_id, '-'), COALESCE(error_class, '-') "
            "FROM publish_attempt",
        )

        print("      멱등 재구동 (이중 발행 0이어야 함)")
        again = run_pending_publications(conn, publish)
        print(f"      재선택된 건수: {len(again)} (0이면 멱등 확인)")

        print("\n[5/5] C6 알림 — run_event 이중적재 + Discord 전송")
        sender = None
        if os.environ.get("DISCORD_WEBHOOK_URL"):
            sender = discord_sender_from_env()
        else:
            print("      DISCORD_WEBHOOK_URL 없음 — DB 적재만 (전송 생략)")
        pub_row = conn.execute("SELECT id, external_post_id FROM publication LIMIT 1").fetchone()
        assert pub_row is not None
        alert = publish_success(
            "instagram", post_id=str(pub_row[1]), publication_id=str(pub_row[0])
        )
        d = dispatch_alert(alert, sink=PgAlertSink(conn), sender=sender)
        print(f"      DB적재={d.recorded} Discord전송={d.delivered}")

        dump(
            conn,
            "run_event (사이클 전체 발자국)",
            "SELECT kind, LEFT(payload::text, 70) FROM run_event ORDER BY created_at",
        )

        print("\n관통 완료. PNG를 열어 실제 카드를 확인하세요:")
        for p in media_store.saved:
            print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
