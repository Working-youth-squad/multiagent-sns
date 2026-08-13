"""E2E: 영상 자동 생성 → 품질 검사 → 유튜브 실 채널 private 업로드 (FR-P2 수용기준).

전제 (YT-1, GCP 콘솔에서 수동):
  1. .secrets/client_secret.json  — OAuth 데스크톱 앱 클라이언트
  2. env GOOGLE_TTS_API_KEY       — Cloud TTS 제한 API 키
  3. 업로드할 유튜브 채널이 있는 계정으로 첫 실행 시 브라우저 동의

실행: uv run python scripts/e2e_youtube_shorts.py [ffmpeg경로]
주의: 업로드 1회 = 쿼터 1,600/10,000 units — 반복 실행 금지.
"""

import sys
from pathlib import Path

from sns.adapters.youtube.auth import build_youtube, load_credentials
from sns.adapters.youtube.publisher import YouTubePublish
from sns.publish.state_machine import run_publish
from sns.publish.stores import InMemoryPublishAttemptStore
from sns.render.video.media import VideoRenderMedia
from sns.render.video.quality import check_video
from sns.render.video.tts import synthesize_google
from sns.tools.contracts import MediaKind

OUT_DIR = Path(__file__).parent / "out"
SECRETS = Path(__file__).parent.parent / ".secrets"

MEDIA_SPEC: dict[str, object] = {
    "slides": [
        {
            "title": "멀티에이전트가 SNS를 키운다면?",
            "body": "사람 손 없이 굴러가는 성장 엔진",
            "narration": "멀티에이전트가 SNS 계정을 직접 키운다면 어떨까요?",
        },
        {
            "title": "영상도 코드로 만든다",
            "body": "자막 · 슬라이드 · TTS 합성",
            "narration": "이 영상도 사람이 편집한 게 아니라 코드가 합성했습니다.",
        },
        {
            "title": "업로드까지 전자동",
            "body": "품질 게이트를 통과해야 발행",
            "narration": "품질 검사를 통과한 영상만 자동으로 업로드됩니다.",
        },
    ],
}
CAPTION = (
    "멀티에이전트 SNS 엔진 — 첫 자동 생성 쇼츠\n"
    "영상 합성과 업로드 전 과정이 코드로 자동화된 파이프라인 E2E 테스트입니다."
)


class DirMediaStore:
    """파일 저장소 — E2E 전용 5줄짜리 (운영 저장소는 FR-M3 후속)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, *, checksum: str, kind: MediaKind, ext: str) -> str:
        path = self.root / f"{kind}-{checksum[:16]}.{ext}"
        path.write_bytes(data)
        return str(path)


def main() -> None:
    ffmpeg = sys.argv[1] if len(sys.argv) > 1 else "ffmpeg"
    ffprobe = str(Path(ffmpeg).parent / "ffprobe") if ffmpeg != "ffmpeg" else "ffprobe"

    print("1/4 영상 렌더 (TTS: Chirp 3 HD)…")
    store = DirMediaStore(OUT_DIR)
    render_media = VideoRenderMedia(store, synthesize=synthesize_google, ffmpeg=ffmpeg)
    asset = render_media(MEDIA_SPEC, "video")
    mp4_path = Path(asset.storage_url)
    print(f"    → {mp4_path} ({mp4_path.stat().st_size // 1024}KiB)")

    print("2/4 품질 검사 (9:16·길이·오디오)…")
    report = check_video(mp4_path.read_bytes(), ffprobe=ffprobe, ffmpeg=ffmpeg)
    if not report.passed:
        raise SystemExit(f"품질 게이트 실패: {report.failures}")
    print("    → 통과")

    print("3/4 유튜브 OAuth (첫 실행은 브라우저 동의)…")
    creds = load_credentials(SECRETS / "client_secret.json", SECRETS / "token.json")
    youtube = build_youtube(creds)

    print("4/4 상태머신 경유 업로드 (privacyStatus=private)…")
    attempt = run_publish(
        store=InMemoryPublishAttemptStore(),
        publish=YouTubePublish(youtube, media_bytes=lambda url: Path(url).read_bytes()),
        publication_id="e2e-first-shorts",
        platform="youtube",
        media=asset,
        caption=CAPTION,
        idempotency_key="e2e-first-shorts",
        quality_passed=report.passed,
    )
    if attempt.state != "published":
        raise SystemExit(f"업로드 실패: {attempt.error_class} — {attempt.error_raw}")
    print(f"성공 — video_id={attempt.external_post_id}")
    print(
        f"https://youtube.com/shorts/{attempt.external_post_id} (private, 유튜브 스튜디오에서 확인)"
    )


if __name__ == "__main__":
    main()
