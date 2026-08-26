"""생성 클립 영상 미리보기 — 팀 4방향 중 ① "영상 클립 자체 생성"의 **독립 진입점**.

온보딩·사이클·DB와 무관하게 spec 하나로 돈다: 컷마다 Veo가 배경 클립을 만들고
(`--call`, 유료), TTS 나레이션 + 하단 자막을 얹어 쇼츠 mp4를 만든다.

실행:
    uv run python scripts/preview_clip_video.py            # 클립 없이 (무료, 폴백 배경)
    uv run python scripts/preview_clip_video.py --call     # Veo 클립 생성 (컷당 8초 과금)

⚠ 비용: veo-3.1-lite 기준 8초 클립 1개 ≈ $0.40 — 아래 spec은 컷 2개 = 클립 2개.
   모델 교체는 env CLIP_GEN_MODEL ([sns.render.clips.generate]).

전제: env GEMINI_API_KEY(결제 켠 키) · TTS 자격증명(GOOGLE_TTS_API_KEY) · ffmpeg PATH.
"""

import argparse
import hashlib
import sys
from pathlib import Path

from dotenv import load_dotenv

from sns.render.clips.generate import ClipGenerationError, build_prompt, generate_clip
from sns.render.video.media import VideoRenderMedia
from sns.render.video.quality import check_video
from sns.render.video.tts import synthesize_google
from sns.tools.contracts import MediaKind

ROOT = Path(__file__).parent.parent
OUT = ROOT / "scripts" / "out" / "clips"

# 컷마다 클립 주제(영문 — 게이트 기준)와 한국어 나레이션. 독립 데모라 하드코딩이다.
CUTS: tuple[tuple[str, str, str], ...] = (
    # (subtitle, narration, clip subject)
    ("갓 구운 빵", "오븐에서 갓 나온 빵이 김을 내고 있어요.",
     "freshly baked bread steaming on a wooden table, warm kitchen light"),
    ("바삭한 단면", "칼로 자르면 단면이 이렇게 바삭합니다.",
     "a knife slicing through crusty bread in slow motion, crumbs falling"),
)  # fmt: skip


class DirMediaStore:
    """클립·영상 바이트를 디스크에 — 사람이 열어볼 수 있게(scripts 공통 5줄짜리)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, *, checksum: str, kind: MediaKind, ext: str) -> str:
        path = self.root / f"{kind}-{checksum[:16]}.{ext}"
        path.write_bytes(data)
        return str(path)

    def get(self, url: str) -> bytes:
        return Path(url).read_bytes()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--call", action="store_true", help="Veo 클립 실제 생성 (컷당 과금)")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 실행 파일 경로")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)

    store = DirMediaStore(OUT)
    slides: list[dict[str, object]] = []
    for i, (subtitle, narration, subject) in enumerate(CUTS, 1):
        slide: dict[str, object] = {"subtitle": subtitle, "narration": narration}
        print(f"[{i}] {build_prompt(subject)}")
        if args.call:
            try:
                clip = generate_clip(subject)
            except ClipGenerationError as exc:
                print(f"    실패 — {exc} (이 컷은 폴백 배경으로 갑니다)")
            else:
                ref = store.put(
                    clip, checksum=hashlib.sha256(clip).hexdigest(), kind="video", ext="mp4"
                )
                slide["clip_ref"] = ref
                print(f"    클립: {ref}")
        slides.append(slide)
    if not args.call:
        print("\n클립 생성 없이 폴백 배경으로 렌더합니다. 실제 생성은 --call (컷당 8초 과금)")

    spec = {"topic": "생성 클립 데모", "style": "clip", "slides": slides}
    print("\n렌더 (TTS + ffmpeg)…")
    # 데모 spec이 요리 소재라 topic_major도 요리 — 주제 분기(정사각 소스·개념 그림)가 맞는다.
    renderer = VideoRenderMedia(
        store, synthesize=synthesize_google, topic_major="요리", ffmpeg=args.ffmpeg
    )
    asset = renderer(spec, "video")
    mp4 = Path(asset.storage_url)
    ffprobe = str(Path(args.ffmpeg).parent / "ffprobe") if args.ffmpeg != "ffmpeg" else "ffprobe"
    report = check_video(mp4.read_bytes(), ffprobe=ffprobe, ffmpeg=args.ffmpeg)
    print(f"품질: {'통과' if report.passed else report.failures}")
    print(f"{mp4.resolve()}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
