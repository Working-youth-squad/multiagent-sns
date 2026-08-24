"""classic 템플릿으로 영상 1편 렌더 — 3단과 눈으로 비교하는 용도.

classic([sns.render.video.classic])은 자동 사이클에 배선돼 있지 않다. 사이클은 3단만
쓴다. 이 스크립트가 classic을 돌리는 **유일한 경로**다 — 두 모양을 나란히 놓고 보거나,
코드·도표가 없는 주제에서 어느 쪽이 나은지 판단할 때.

실행:
    uv run python scripts/render_classic.py --sample              # 내장 예시
    uv run python scripts/render_classic.py spec.json             # media_spec JSON
    uv run python scripts/render_classic.py --sample --silent     # TTS 없이(무음)

`--silent`는 TTS 자격증명 없이 **화면만** 보려는 경우다. 무음 WAV를 글자 수에 비례한
길이로 만들어 넣으므로 타이밍은 실물과 비슷하지만 같지는 않다. 레이아웃 확인용이다.

media_spec 모양은 3단과 **다르다**(classic은 title/body, 3단은 subtitle/code).
3단 spec을 여기 넣으면 VideoSpecError로 끊긴다 — 그게 맞는 동작이다.
"""

import argparse
import io
import json
import sys
import wave
from pathlib import Path

from dotenv import load_dotenv

from sns.render.video.classic import VideoSpecError, parse_video_spec, render_video
from sns.render.video.tts import SAMPLE_RATE_HZ, synthesize_google

ROOT = Path(__file__).parent.parent
OUT = ROOT / "scripts" / "out" / "classic"

# 코드도 도표도 없는 주제 — classic이 아직 쓸모 있다면 이런 자리다.
SAMPLE_SPEC: dict[str, object] = {
    "slides": [
        {
            "title": "주니어가 놓치는 것",
            "body": "코드가 아니라 맥락이다",
            "narration": "주니어 개발자가 가장 많이 놓치는 건 코드 실력이 아닙니다.",
        },
        {
            "title": "왜 이걸 하는가",
            "body": "요구사항 뒤의 진짜 문제",
            "narration": "티켓에 적힌 요구사항 뒤에는 항상 진짜 문제가 숨어 있습니다.",
        },
        {
            "title": "물어보는 값",
            "body": "30초의 질문이 3일을 아낀다",
            "narration": "삼십 초짜리 질문 하나가 사흘치 삽질을 막아줍니다.",
        },
    ],
}


def silent_wav(text: str, *, voice: str) -> bytes:
    """무음 Synthesize — 글자 수에 비례한 길이. TTS 자격증명 없이 화면만 볼 때."""
    duration_s = 1.0 + 0.12 * len(text)
    frames = int(duration_s * SAMPLE_RATE_HZ)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE_HZ)
        f.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description="classic 템플릿으로 영상 렌더")
    ap.add_argument("spec", nargs="?", type=Path, help="media_spec JSON 파일")
    ap.add_argument("--sample", action="store_true", help="내장 예시 spec 사용")
    ap.add_argument("--silent", action="store_true", help="TTS 대신 무음(자격증명 불필요)")
    ap.add_argument("-o", "--out", type=Path, help="출력 mp4 (기본: scripts/out/classic/)")
    ap.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 실행 파일")
    args = ap.parse_args()

    if not args.sample and args.spec is None:
        ap.error("spec 파일을 주거나 --sample 을 쓰세요")

    load_dotenv(ROOT / ".env")

    if args.sample:
        media_spec: dict[str, object] = SAMPLE_SPEC
        stem = "sample"
    else:
        media_spec = json.loads(args.spec.read_text(encoding="utf-8"))
        stem = args.spec.stem

    try:
        spec = parse_video_spec(media_spec)
    except VideoSpecError as exc:
        print(f"spec 오류: {exc}", file=sys.stderr)
        print("classic은 slides[].title/body/narration 모양입니다 (3단과 다름).", file=sys.stderr)
        return 1

    synthesize = silent_wav if args.silent else synthesize_google
    print(f"렌더 중… 슬라이드 {len(spec.slides)}장, TTS={'무음' if args.silent else 'Google'}")
    render = render_video(spec, synthesize=synthesize, ffmpeg=args.ffmpeg)

    out = args.out or (OUT / f"{stem}.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(render.mp4)

    print(f"→ {out}  ({len(render.mp4) // 1024}KiB, {render.duration_s:.1f}s)")
    print(
        f"  컷 {len(render.cut_durations_s)}개, 화면 세그먼트 {len(render.segment_durations_s)}개"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
