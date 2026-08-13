"""C4 렌더러 스파이크 A: Pillow 슬라이드 + ASS 자막 + ffmpeg 합성.

실행: uv run python spikes/c4-renderer/ffmpeg_sample.py [ffmpeg경로]
산출: spikes/c4-renderer/out/ffmpeg_sample.mp4 (+ 결정론 검사용 2차 렌더 checksum 비교)
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1080, 1920
SLIDE_SECONDS = 5
FONT_PATH = r"C:\Windows\Fonts\malgunbd.ttf"
SLIDES = [
    ("멀티에이전트 SNS", "자율 성장 엔진이 뭐냐면"),
    ("영상도 코드로 만든다", "자막 + 슬라이드 + TTS 합성"),
    ("생성형 비디오 모델?", "안 씁니다. 템플릿 코드 합성."),
]
# safe area: 중앙 900×1400 → 좌우 마진 90, 상하 마진 260
SAFE_MARGIN_X = (WIDTH - 900) // 2
SAFE_MARGIN_V = (HEIGHT - 1400) // 2


def make_slide(title: str, out: Path) -> None:
    img = Image.new("RGB", (WIDTH, HEIGHT), "#101828")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, 88)
    box = draw.textbbox((0, 0), title, font=font)
    x = (WIDTH - (box[2] - box[0])) // 2
    y = (HEIGHT - (box[3] - box[1])) // 2 - 120
    draw.text((x, y), title, font=font, fill="#F9FAFB")
    img.save(out)


def ass_time(seconds: float) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass() -> str:
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {WIDTH}\n"
        f"PlayResY: {HEIGHT}\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
        f"Style: Default,Malgun Gothic,64,&H00FFFFFF,&H00000000,&H80000000,"
        f"-1,3,0,2,{SAFE_MARGIN_X},{SAFE_MARGIN_X},{SAFE_MARGIN_V}\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for i, (_, subtitle) in enumerate(SLIDES):
        start, end = i * SLIDE_SECONDS, (i + 1) * SLIDE_SECONDS
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{subtitle}")
    return header + "\n".join(lines) + "\n"


def render(ffmpeg: str, workdir: Path) -> bytes:
    for i, (title, _) in enumerate(SLIDES):
        make_slide(title, workdir / f"slide{i}.png")
    concat = "".join(f"file 'slide{i}.png'\nduration {SLIDE_SECONDS}\n" for i in range(len(SLIDES)))
    # concat demuxer 규칙: 마지막 파일은 한 번 더
    concat += f"file 'slide{len(SLIDES) - 1}.png'\n"
    (workdir / "list.txt").write_text(concat, encoding="utf-8")
    (workdir / "subs.ass").write_text(build_ass(), encoding="utf-8")
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        "list.txt",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=44100:cl=stereo",
        "-vf",
        "subtitles=subs.ass,format=yuv420p",
        "-r",
        "30",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-c:a",
        "aac",
        "-t",
        str(len(SLIDES) * SLIDE_SECONDS),
        "-fflags",
        "+bitexact",
        "-flags:v",
        "+bitexact",
        "-flags:a",
        "+bitexact",
        "-bitexact",
        "-map_metadata",
        "-1",
        "-threads",
        "1",
        "out.mp4",
    ]
    subprocess.run(cmd, cwd=workdir, check=True)
    return (workdir / "out.mp4").read_bytes()


def main() -> None:
    ffmpeg = sys.argv[1] if len(sys.argv) > 1 else "ffmpeg"
    out_dir = Path(__file__).parent / "out"
    out_dir.mkdir(exist_ok=True)
    checksums = []
    for run in range(2):
        with tempfile.TemporaryDirectory() as tmp:
            t0 = time.perf_counter()
            mp4 = render(ffmpeg, Path(tmp))
            elapsed = time.perf_counter() - t0
        checksums.append(hashlib.sha256(mp4).hexdigest())
        print(f"run {run + 1}: {elapsed:.1f}s, {len(mp4) // 1024}KiB, sha256={checksums[-1][:16]}…")
        if run == 0:
            (out_dir / "ffmpeg_sample.mp4").write_bytes(mp4)
    print("deterministic:", checksums[0] == checksums[1])


if __name__ == "__main__":
    main()
