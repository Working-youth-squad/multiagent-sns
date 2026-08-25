"""온보딩 프로필 채널의 게시물 생성 사이클 — 인터뷰 결과가 실제 콘텐츠가 되는 연결부.

uv run python scripts/run_profile_cycle.py <채널핸들>

`channel_profile` 최신 revision을 읽어 사이클에 주입한다:
  goal_ref            → run_cycle(goal_ref=...)          (goal 프리셋 실배선)
  categories          → run_cycle(topic_categories=...)  (주제 카테고리 교체)
  brief(주제범위·톤·캐릭터) → channel_brief(Topic) + playbook_guidance(Content)

**채널당 전용 사이클**(targets 1개)로 돈다 — 기존 실험 사이클(변수=mode 하나,
동일 주제 도메인 공유)과 실행 단위를 분리해 통제 설계를 깨지 않는다.

트렌드는 default_service() 그대로 조립한다 — 프로필 맞춤 트렌드(세부주제 쿼리
바인딩)는 트렌드 담당의 상세 버전이 오면 아래 `trends = ...` 한 줄만 교체.

발행은 e2e_cycle과 동일하게 FakePublish로 원장 종결까지만 확인한다(실 어댑터
연결은 발행 러너 운영 배선 몫). 포맷은 feed_image(카드) — 영상 포맷은 TTS·ffmpeg
조립이 필요해 후속(scripts/e2e_youtube_pipeline.py의 조립 재사용 예정).

전제: docker compose up -d postgres · env GEMINI_API_KEY · (선택) DATABASE_URL, CARD_FONT
"""

import os
import sys
from collections.abc import Mapping
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from sns.agents.models import make_model
from sns.onboarding.profile import build_channel_brief
from sns.onboarding.store import PgOnboardingStore
from sns.publish.runner import run_pending_publications
from sns.quality.gate import QualityReport, check_card
from sns.render.card.media import CardRenderMedia
from sns.render.card.spec import parse_card_spec
from sns.research.trends import default_service
from sns.runner.cycle import CycleTarget, run_cycle
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
    """렌더 바이트를 디스크에 — 사람이 열어볼 수 있게(scripts/e2e_cycle.py와 동형)."""

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
        raise NotImplementedError("이 스크립트는 되읽기를 쓰지 않는다")


def find_font() -> str | None:
    env = os.environ.get("CARD_FONT")
    if env:
        return env
    return next((c for c in FONT_CANDIDATES if Path(c).exists()), None)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) != 2:
        print("사용법: uv run python scripts/run_profile_cycle.py <채널핸들>")
        return 2
    handle = sys.argv[1]

    load_dotenv(ENV_FILE, override=False)
    if not os.environ.get("GEMINI_API_KEY"):
        print("중단: env GEMINI_API_KEY 없음 — https://aistudio.google.com/apikey")
        return 1

    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    try:
        conn = psycopg.connect(dsn, connect_timeout=10, autocommit=True)
    except psycopg.OperationalError as exc:
        print(f"중단: PostgreSQL 연결 실패 — docker compose up -d postgres\n      {exc}")
        return 1

    with conn:
        onboarding = PgOnboardingStore(conn)
        channel = next((c for c in onboarding.list_channels() if c.handle == handle), None)
        if channel is None:
            print(f"중단: 채널 {handle!r} 없음 — 온보딩 웹(run_onboarding_web.py)에서 먼저 생성")
            return 1
        profile = onboarding.latest_profile(channel.channel_id)
        if profile is None:
            print(f"중단: 채널 {handle!r}에 프로필 없음 — 온보딩 인터뷰를 먼저 완료")
            return 1

        brief = build_channel_brief(profile)
        print(f"채널  : {channel.handle} ({channel.platform}, {channel.mode})")
        print(f"프로필: {profile.topic_major} / {', '.join(profile.topic_subs)}")
        print(f"goal  : {profile.goal_ref}")
        print(f"brief :\n{brief}\n")

        media_store = DirMediaStore(OUT)
        renderer = CardRenderMedia(media_store, font_path=find_font())

        def assess(
            *,
            media_spec: Mapping[str, object],
            media: MediaAsset,
            content_format: ContentFormat,
        ) -> QualityReport:
            return check_card(parse_card_spec(media_spec), renderer.render(media_spec))

        # 프로필 맞춤 트렌드 조립은 트렌드 담당 몫 — 완성되면 이 줄만 교체한다.
        trends = default_service()

        result = run_cycle(
            PgCycleStore(conn),
            goal_ref=profile.goal_ref,
            targets=[
                CycleTarget(
                    channel_id=channel.channel_id,
                    platform="instagram" if channel.platform == "instagram" else "youtube",
                    content_format="feed_image",
                    mode="hybrid" if channel.mode == "hybrid" else "auto",
                )
            ],
            model=make_model(),
            research_trends=trends,
            read_stats=FakeReadStats(),
            render_media=renderer,
            assess_quality=assess,
            channel_brief=brief,
            topic_categories=profile.categories,
            playbook_guidance=brief,
        )
        print(f"cycle={result.cycle_id} status={result.status}")
        for t in result.targets:
            print(f"  {t.channel_id[:8]}... -> {t.outcome} {t.error or ''}")
        for p in media_store.saved:
            print(f"  렌더 산출물: {p}")
        if result.status != "completed":
            return 1

        if channel.mode == "hybrid":
            print("\nhybrid 채널 — 승인 화면(run_approve_web.py)에서 검수 후 발행됩니다.")
        else:
            print("\n발행 러너 (FakePublish — 원장 전이 확인)")
            for o in run_pending_publications(conn, FakePublish()):
                print(f"  {o}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
