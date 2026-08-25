"""E2E: 파이프라인이 만든 쇼츠를 실제 유튜브에 올린다 (C1·C2·C4 → YT-2).

기존 `e2e_youtube_shorts.py`와 다른 점: 대본이 **하드코딩이 아니라 에이전트 산출물**이다.
실 트렌드 → 실 Gemini(주제·대본) → 실 TTS 영상 → 실 품질 게이트 → 실 업로드까지 간다.

**원장(Postgres)이 기본이다.** 예전엔 InMemoryCycleStore로 돌렸는데, 그러면 매 실행이
기억을 잃어 주제 중복 차단이 동작하지 않는다 — 실제로 2026-08-20/21 이틀 연속 같은
Cursor 영상이 나갔다. --store memory로 끌 수 있지만 그때는 경고를 찍는다.

전제:
  1. env GEMINI_API_KEY          — aistudio.google.com/apikey
  2. TTS 자격증명 — GOOGLE_TTS_API_KEY 또는 ADC
     (gcloud auth application-default login)
  3. .secrets/client_secret.json — GCP 콘솔 > 사용자 인증 정보 >
     OAuth 클라이언트 ID > **데스크톱 앱** 유형으로 만들어 다운로드
  4. ffmpeg/ffprobe가 PATH에 있거나 --ffmpeg로 경로 지정
  5. OAuth 계정에 **유튜브 채널이 있어야** 한다

실행:
    uv run python scripts/e2e_youtube_pipeline.py             # 렌더까지만 (업로드 안 함)
    uv run python scripts/e2e_youtube_pipeline.py --upload    # 실제 업로드

⚠️ 업로드 1회 = 쿼터 1,600 / 일 10,000 units → **하루 6회가 상한**이다. 반복 실행 금지.
   첫 업로드는 privacyStatus=private. 미감사 API 프로젝트는 어차피 private 잠금이다.
"""

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from sns.adapters.youtube.auth import build_youtube, load_credentials
from sns.adapters.youtube.publisher import YouTubePublish, split_caption
from sns.agents.models import make_model
from sns.publish.state_machine import run_publish
from sns.publish.stores import InMemoryPublishAttemptStore
from sns.quality.gate import QualityCheck, QualityReport
from sns.render.images.resolve import resolve_images
from sns.render.video.media import VideoRenderMedia
from sns.render.video.quality import check_video
from sns.render.video.tts import synthesize_google
from sns.research.trends import default_service
from sns.runner.cycle import AssessQuality, CycleTarget, run_cycle
from sns.runner.store import CycleStore, InMemoryCycleStore, PgCycleStore
from sns.tools.contracts import ContentFormat, MediaAsset, MediaKind
from sns.tools.fakes import FakeReadStats

ROOT = Path(__file__).parent.parent
OUT = ROOT / "scripts" / "out" / "yt"
SECRETS = ROOT / ".secrets"
UPLOAD_QUOTA_UNITS = 1600


class DirMediaStore:
    """렌더 바이트를 디스크에 저장하고 **경로 문자열**을 URL로 돌려준다.

    업로드 시 `Path(storage_url).read_bytes()`로 되읽으므로 file:// URI가 아니라 평문 경로다.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, *, checksum: str, kind: MediaKind, ext: str) -> str:
        path = self.root / f"{kind}-{checksum[:16]}.{ext}"
        path.write_bytes(data)
        return str(path)

    def get(self, url: str) -> bytes:
        return Path(url).read_bytes()


def make_gate(ffprobe: str, ffmpeg: str) -> AssessQuality:
    """영상 품질 게이트를 AssessQuality 형태로 조립."""

    def assess(
        *, media_spec: Mapping[str, object], media: MediaAsset, content_format: ContentFormat
    ) -> QualityReport:
        report = check_video(Path(media.storage_url).read_bytes(), ffprobe=ffprobe, ffmpeg=ffmpeg)
        checks = tuple(QualityCheck(f, False, f) for f in report.failures) or (
            QualityCheck("video_spec", True, "규격·길이·오디오 하한 통과"),
        )
        return QualityReport(status="passed" if report.passed else "failed", checks=checks)

    return assess


def ensure_channel(conn: psycopg.Connection, *, handle: str) -> str:
    """유튜브 채널 행 1개 확보(있으면 재사용) — publication의 FK 대상.

    인메모리 저장소는 channel_id로 아무 문자열이나 받았지만 원장은 UUID FK다.
    저장소를 Postgres로 바꾸면서 드러났다.
    """
    row = conn.execute("SELECT id FROM channel WHERE handle = %s", (handle,)).fetchone()
    if row is None:
        row = conn.execute(
            "INSERT INTO channel (platform, handle) VALUES ('youtube', %s) RETURNING id",
            (handle,),
        ).fetchone()
    assert row is not None
    return str(row[0])


def sidecar(mp4: Path) -> Path:
    """mp4 옆의 캡션·주제 저장 경로."""
    return mp4.with_suffix(".json")


def upload_asset(media: MediaAsset, caption: str, *, upload: bool) -> int:
    """공통 업로드 경로 — 상태머신 경유, privacyStatus=private."""
    title, description = split_caption(caption)
    print(f"\n      유튜브 제목: {title}")
    print(f"      설명       : {description[:200]}{'…' if len(description) > 200 else ''}")
    if not upload:
        print(
            f"\n업로드 생략 (기본값). 실제로 올리려면 --upload"
            f"\n  쿼터 {UPLOAD_QUOTA_UNITS:,} units 소모 / 일 10,000 → 하루 6회 상한"
        )
        return 0

    client_secret = SECRETS / "client_secret.json"
    if not client_secret.exists():
        print(f"중단: {client_secret} 없음 — 데스크톱 앱 OAuth 클라이언트 JSON이 필요합니다")
        return 1
    print(f"\n업로드 (쿼터 {UPLOAD_QUOTA_UNITS:,} units, privacyStatus=private)")
    print("  OAuth… (첫 실행은 브라우저 동의)")
    youtube = build_youtube(load_credentials(client_secret, SECRETS / "token.json"))
    attempt = run_publish(
        store=InMemoryPublishAttemptStore(),
        publish=YouTubePublish(youtube, media_bytes=lambda url: Path(url).read_bytes()),
        publication_id=media.checksum,  # 콘텐츠 해시 = 안정적 멱등 키
        platform="youtube",
        media=media,
        caption=caption,
        idempotency_key=media.checksum,
        quality_passed=True,
    )
    if attempt.state != "published":
        print(f"업로드 실패: {attempt.error_class} — {attempt.error_raw}")
        return 1
    print(f"\n성공 — video_id={attempt.external_post_id}")
    print(f"https://youtube.com/shorts/{attempt.external_post_id}  (private, 스튜디오에서 확인)")
    return 0


def upload_existing(mp4: Path, caption_file: str | None, *, upload: bool) -> int:
    """이미 렌더된 mp4를 올린다. 캡션은 사이드카 또는 --caption-file에서 읽는다."""
    if not mp4.exists():
        print(f"중단: {mp4} 없음")
        return 1
    if caption_file:
        caption, source = Path(caption_file).read_text(encoding="utf-8").strip(), caption_file
    elif sidecar(mp4).exists():
        caption = str(json.loads(sidecar(mp4).read_text(encoding="utf-8"))["caption"])
        source = sidecar(mp4).name
    else:
        print(f"중단: 캡션 없음 — {sidecar(mp4).name} 또는 --caption-file 이 필요합니다")
        return 1
    print(f"기존 파일 업로드: {mp4}  ({mp4.stat().st_size // 1024:,}KiB)")
    print(f"캡션 출처      : {source}")
    media = MediaAsset(
        kind="video", storage_url=str(mp4), checksum=hashlib.sha256(mp4.read_bytes()).hexdigest()
    )
    return upload_asset(media, caption, upload=upload)


def main() -> int:
    # Windows 콘솔 기본 코드페이지(cp949)는 em대시·한글 일부를 못 찍어 UnicodeEncodeError로
    # 죽는다. 업로드가 끝난 뒤 성공 메시지 출력에서 터져 video_id를 잃은 적이 있다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--upload", action="store_true", help="실제 유튜브 업로드 (쿼터 1,600 소모)"
    )
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 실행 파일 경로")
    parser.add_argument("--file", default=None, help="기존 mp4 업로드 (렌더·에이전트 건너뜀)")
    parser.add_argument("--caption-file", default=None, help="--file과 함께 쓸 캡션 텍스트 파일")
    parser.add_argument("--font", default=None, help="한글 TTF 경로 (미지정 시 자동 탐색)")
    parser.add_argument(
        "--store", choices=("pg", "memory"), default="pg",
        help="원장 저장소. pg(기본)라야 주제 중복 차단이 동작한다",
    )  # fmt: skip
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)
    if not os.environ.get("GEMINI_API_KEY"):
        print("중단: env GEMINI_API_KEY 없음 — aistudio.google.com/apikey")
        return 1

    ffmpeg = args.ffmpeg
    ffprobe = str(Path(ffmpeg).parent / "ffprobe") if ffmpeg != "ffmpeg" else "ffprobe"

    if args.file:
        return upload_existing(Path(args.file), args.caption_file, upload=args.upload)

    media_store = DirMediaStore(OUT)
    renderer = VideoRenderMedia(
        media_store, synthesize=synthesize_google, font_path=args.font, ffmpeg=ffmpeg
    )
    conn = None
    store: CycleStore
    channel_id = "yt-pipeline-test"  # 인메모리는 임의 문자열이면 된다
    if args.store == "memory":
        print("⚠ 원장 없이 실행 — 과거 발행 이력을 못 읽어 **주제 중복 차단이 꺼집니다**")
        store = InMemoryCycleStore()
    else:
        try:
            conn = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10, autocommit=True)
        except (KeyError, psycopg.OperationalError) as exc:
            print("중단: 원장(PostgreSQL) 연결 실패 — docker compose up -d postgres")
            print(f"      {exc}")
            print("      원장 없이 돌리려면 --store memory (주제 중복 차단이 꺼집니다)")
            return 1
        store = PgCycleStore(conn)
        channel_id = ensure_channel(conn, handle="yt-pipeline-test")
        recent = store.recent_topic_titles(days=14)
        print(f"      최근 14일 발행 주제 {len(recent)}건 — 후보에서 제외됩니다")

    print("[1/4] 실 트렌드 조회")
    trends = default_service()
    digest = trends(limit=10)
    for r in digest.source_results:
        if r.ok and r.items:
            print(f"      {r.source}: {', '.join(r.items[:5])}")

    print("\n[2/4] 사이클 구동 — Gemini 주제·대본 → 실 TTS 영상 렌더")
    result = run_cycle(
        store,
        goal_ref="engagement_depth",
        targets=[CycleTarget(channel_id=channel_id, platform="youtube", content_format="shorts")],
        model=make_model(),
        research_trends=trends,
        read_stats=FakeReadStats(),
        render_media=renderer,
        assess_quality=make_gate(ffprobe, ffmpeg),
        # 사진 해소 seam — PEXELS_API_KEY가 없으면 후보를 못 구해 notice만 남고
        # 그라데이션/개념 그림으로 간다(영상은 그대로 나온다).
        resolve_media_spec=lambda spec: resolve_images(spec, store=media_store),
    )
    if result.status != "completed" or not result.prepared:
        print(f"      사이클 실패: status={result.status}")
        for t in result.targets:
            print(f"      {t.outcome} — {t.error}")
        for e in store.events:
            if e.get("kind") == "error":
                print(f"      event: {e.get('payload')}")
        return 1

    target = result.prepared[0]
    assert target.content_item_id and target.media_asset_id
    item = store.read_content_item(target.content_item_id)
    asset_row = store.read_media_asset(target.media_asset_id)
    topic = store.read_topic(str(result.topic_id))

    caption = str(item["body"])
    asset = MediaAsset(
        kind="video",
        storage_url=str(asset_row["storage_url"]),
        checksum=str(asset_row["checksum"]),
    )
    mp4 = Path(asset.storage_url)

    print("\n[3/4] 산출물 — 이게 올라갑니다")
    print(f"      주제      : {topic['title']}")
    print(f"      훅 패턴   : {item['hook_pattern']}")
    print(f"      품질      : {asset_row['quality_status']}")
    print(f"      파일      : {mp4}  ({mp4.stat().st_size // 1024:,}KiB)")
    spec = item["media_spec"]
    if isinstance(spec, Mapping):
        slides = spec.get("slides")
        if isinstance(slides, list):
            print(f"      주제      : {spec.get('topic', '')}")
            print(f"      슬라이드  : {len(slides)}장")
            for i, s in enumerate(slides, 1):
                if isinstance(s, Mapping):
                    mark = " [코드]" if str(s.get("code", "")).strip() else ""
                    print(f"        {i}. {s.get('subtitle', '')}{mark}")
    # 캡션을 mp4 옆에 남긴다 — 프로세스가 끝나도 "무엇을 올릴지"를 잃지 않게.
    # (InMemoryCycleStore는 프로세스와 함께 사라진다. 실제로 한 번 잃어봤다.)
    sidecar(mp4).write_text(
        json.dumps(
            {"caption": caption, "topic": topic["title"], "media_spec": spec},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"      캡션 저장 : {sidecar(mp4).name}")

    if asset_row["quality_status"] != "passed":
        print("\n중단: 품질 게이트 미통과 — 업로드하지 않습니다.")
        return 1

    return upload_asset(asset, caption, upload=args.upload)


if __name__ == "__main__":
    sys.exit(main())
