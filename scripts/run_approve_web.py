"""hybrid 승인 웹 서버 기동 (C9). uv run python scripts/run_approve_web.py

전제:
  1. docker compose up -d postgres
  2. env DATABASE_URL — (선택) 기본 postgresql://sns:sns@localhost:5432/sns
  3. env APPROVE_WEB_HOST/PORT — (선택) 기본 127.0.0.1:8001
  4. 영상 재렌더(선택): TTS 자격증명(GOOGLE_TTS_API_KEY 또는 ADC) + ffmpeg/ffprobe.
     없어도 서버는 뜬다 — 재렌더 버튼을 눌렀을 때 오류 메시지로 표면화된다.

단일 커넥션 전제(scripts/e2e_cycle.py와 동일 규율): 승인 화면은 저트래픽 내부
도구라 커넥션 풀 없이 앱 수명 동안 커넥션 1개를 autocommit으로 연다.
"""

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import psycopg
import uvicorn
from dotenv import load_dotenv

from sns.render.video.media import VideoRenderMedia
from sns.render.video.quality import check_video
from sns.render.video.tts import synthesize_google
from sns.tools.contracts import MediaAsset, MediaKind
from sns.web.approve.app import RerenderVideo, create_app
from sns.web.approve.store import PgApprovalStore

ENV_FILE = Path(__file__).parent.parent / ".env"
DEFAULT_DSN = "postgresql://sns:sns@localhost:5432/sns"
OUT = Path(__file__).parent / "out"
FONT_CANDIDATES = (
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
)


class DirMediaStore:
    """file:// URI 저장소 — run_profile_cycle.py와 같은 디렉터리·같은 규약."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, *, checksum: str, kind: MediaKind, ext: str) -> str:
        path = self.root / f"{kind}-{checksum[:16]}.{ext}"
        path.write_bytes(data)
        return path.resolve().as_uri()

    def get(self, url: str) -> bytes:
        return Path(url2pathname(urlparse(url).path)).read_bytes()


def make_rerender(ffmpeg: str, font: str | None, topic_major: str) -> RerenderVideo:
    """컷 수정 → 재렌더 → 품질 게이트. 실패는 예외로 — 앱이 오류 메시지로 표면화한다."""
    media_store = DirMediaStore(OUT)
    ffprobe = str(Path(ffmpeg).parent / "ffprobe") if ffmpeg != "ffmpeg" else "ffprobe"
    renderer = VideoRenderMedia(
        media_store,
        synthesize=synthesize_google,
        topic_major=topic_major,
        font_path=font,
        ffmpeg=ffmpeg,
    )

    def rerender(
        media_spec: Mapping[str, object],
    ) -> tuple[MediaAsset, str, Mapping[str, object] | None]:
        asset = renderer(media_spec, "video")
        report = check_video(media_store.get(asset.storage_url), ffprobe=ffprobe, ffmpeg=ffmpeg)
        status = "passed" if report.passed else "failed"
        return asset, status, {"passed": report.passed, "failures": list(report.failures)}

    return rerender


def build_app(conn: psycopg.Connection, *, ffmpeg: str, font: str | None, topic_major: str):
    """배선된 승인 FastAPI 앱 — 단독 실행(main)과 통합 서버(run_web.py)가 공유."""
    resolved = font or os.environ.get("CARD_FONT")
    if not resolved:
        resolved = next((c for c in FONT_CANDIDATES if Path(c).exists()), None)
    return create_app(
        PgApprovalStore(conn),
        rerender_video=make_rerender(ffmpeg, resolved, topic_major),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 실행 파일 경로")
    parser.add_argument("--font", default=None, help="한글 TTF 경로 (미지정 시 자동 탐색)")
    # 기본값을 두지 않는다 — 컷 검증(정사각 소스·개념 그림)이 주제마다 다르고, 틀리면
    # 재렌더가 조용히 다른 규칙으로 돈다([sns.topic_policy]).
    parser.add_argument(
        "--topic-major", required=True, help="재렌더할 채널의 주제 대분류 (예: 개발, 요리)"
    )
    args = parser.parse_args()

    load_dotenv(ENV_FILE, override=False)
    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    try:
        conn = psycopg.connect(dsn, connect_timeout=10, autocommit=True)
    except psycopg.OperationalError as exc:
        print(f"중단: PostgreSQL 연결 실패 — docker compose up -d postgres\n      {exc}")
        return 1

    app = build_app(conn, ffmpeg=args.ffmpeg, font=args.font, topic_major=args.topic_major)
    host = os.environ.get("APPROVE_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("APPROVE_WEB_PORT", "8001"))
    print(f"승인 화면: http://{host}:{port}/")
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
