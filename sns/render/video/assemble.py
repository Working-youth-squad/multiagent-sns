"""컷 자산 → mp4 조립 — 렌더 트랙이 공유하는 단 하나의 조립기.

렌더러는 **재료를 만들고**(컷 PNG), 조립기는 **그것을 mp4로 만든다**(concat · 진행바 ·
오디오 · `-bitexact`). 트랙마다 그리는 화면은 다르지만 조립은 같다 — 3단 템플릿이든
풀블리드 생성 장면이든 컷 PNG들과 WAV들을 이어 붙이는 일은 똑같다.

**나눈 이유는 복붙 방지다.** 두 벌이 되면 진행바 좌표나 `-bitexact` 플래그를 한쪽만
고치는 날이 오고, 그날 한 트랙의 결정론이 조용히 깨진다.

모든 ffmpeg 호출은 `-bitexact`·단일 스레드 — 같은 입력(같은 컷 PNG, 같은 TTS 바이트)
→ 같은 mp4(FR-M1).
"""

import io
import subprocess
import tempfile
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sns.render.video.spec import VideoSpec

FPS = 30
# 진행바 높이(px, 1080 기준 비율로 환산).
_BAR_RATIO = 12 / 1920

_BITEXACT = (
    "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
    "-bitexact", "-map_metadata", "-1", "-threads", "1",
)  # fmt: skip


@dataclass(frozen=True)
class VideoRender:
    mp4: bytes
    duration_s: float
    # 컷(=슬라이드=화면) 단위 길이. 합이 곧 오디오 길이다.
    cut_durations_s: tuple[float, ...]


class VideoRenderError(RuntimeError):
    """ffmpeg 합성 실패."""


def _concat_wavs(wavs: Sequence[bytes]) -> bytes:
    """같은 포맷의 WAV들을 프레임 이어붙이기 — ffmpeg 오디오 concat 불필요."""
    first_params = None
    frames = b""
    for wav in wavs:
        with wave.open(io.BytesIO(wav)) as f:
            params = (f.getnchannels(), f.getsampwidth(), f.getframerate())
            if first_params is None:
                first_params = params
            elif params != first_params:
                raise VideoRenderError(f"TTS WAV 포맷 불일치: {first_params} vs {params}")
            frames += f.readframes(f.getnframes())
    assert first_params is not None
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(first_params[0])
        out.setsampwidth(first_params[1])
        out.setframerate(first_params[2])
        out.writeframes(frames)
    return buf.getvalue()


def _run_ffmpeg(cmd: list[str], workdir: Path) -> None:
    result = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    if result.returncode != 0:
        raise VideoRenderError(f"ffmpeg 실패(exit {result.returncode}): {result.stderr.strip()}")


def concat_cuts(
    workdir: Path,
    *,
    cut_count: int,
    wavs: Sequence[bytes],
    total: float,
    width: int,
    accent: str,
    bar_h: int,
    ffmpeg: str = "ffmpeg",
    bgm: bytes | None = None,
    bgm_ext: str = "mp3",
) -> bytes:
    """2패스 공용부: `workdir`의 `cut{i}.mp4` → concat + 진행바 + 오디오.

    **컷 재료를 어떻게 만들었는지는 묻지 않는다.** 3단·모션·생성 장면이 각자 다른
    방식으로 컷 mp4를 만들고(정지 PNG, 줌, 풀블리드 장면) 여기서 합류한다. 진행바
    좌표와 `-bitexact`가 한 벌뿐이라는 게 이 자리의 값이다.

    목록은 반드시 1패스가 만든 컷 수와 같아야 한다 — 적게 쓰면 영상만 조용히 잘리고
    오디오는 그대로라 뒷부분이 정지 화면이 된다.
    """
    (workdir / "list.txt").write_text(
        "".join(f"file 'cut{i}.mp4\n" for i in range(cut_count)), encoding="utf-8"
    )
    (workdir / "audio.wav").write_bytes(_concat_wavs(wavs))

    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", "list.txt",
        "-i", "audio.wav",
        "-f", "lavfi", "-t", f"{total:.3f}",
        "-i", f"color=c=0x{accent[1:]}:s={width}x{bar_h}:r={FPS}",
    ]  # fmt: skip
    # 진행바: 화면 폭짜리 색 소스를 왼쪽 밖에서 밀어넣어 차오르게 한다. drawbox로는
    # 안 된다 — drawbox 표현식의 `t`는 타임스탬프가 아니라 **선 두께**라 매 프레임
    # 같은 값이 나와 바가 처음부터 꽉 찬 채로 멈춘다. overlay의 `x`는 `t`가 시각이다.
    chain = f"[0:v][2:v]overlay=x='-W+W*t/{total:.3f}':y=H-{bar_h}:shortest=1,format=yuv420p[v]"
    if bgm is not None:
        (workdir / f"bgm.{bgm_ext}").write_bytes(bgm)
        cmd += ["-stream_loop", "-1", "-i", f"bgm.{bgm_ext}"]
        cmd += [
            "-filter_complex",
            f"{chain};[1:a]volume=1.0[nar];[3:a]volume=0.12[bg];"
            "[nar][bg]amix=inputs=2:duration=first:normalize=0[a]",
            "-map", "[v]", "-map", "[a]",
        ]  # fmt: skip
    else:
        cmd += ["-filter_complex", chain, "-map", "[v]", "-map", "1:a"]
    cmd += [
        "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
        "-c:a", "aac", "-t", f"{total:.3f}",
        *_BITEXACT, "out.mp4",
    ]  # fmt: skip
    _run_ffmpeg(cmd, workdir)
    return (workdir / "out.mp4").read_bytes()


def assemble_video(
    cut_pngs: Sequence[bytes],
    durations: Sequence[float],
    wavs: Sequence[bytes],
    *,
    spec: VideoSpec,
    ffmpeg: str = "ffmpeg",
    bgm: bytes | None = None,
    bgm_ext: str = "mp3",
) -> VideoRender:
    """컷 PNG들 + 컷 길이 + TTS WAV들 → mp4.

    `durations`는 호출자가 이미 잰 값을 받는다 — WAV에서 다시 재면 같은 값을 두 번
    계산하게 되고, 한쪽만 반올림이 달라지면 영상과 오디오 길이가 어긋난다.
    """
    total = sum(durations)
    bar_h = max(round(spec.height * _BAR_RATIO), 4)

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)

        # 1패스: 컷당 정지 영상. 줌이 없으므로 오버스캔도 세그먼트 등분도 없다.
        for i, (png, duration) in enumerate(zip(cut_pngs, durations, strict=True)):
            (workdir / f"f{i}.png").write_bytes(png)
            _run_ffmpeg(
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-loop",
                    "1",
                    "-t",
                    f"{duration:.3f}",
                    "-i",
                    f"f{i}.png",
                    "-vf",
                    "format=yuv420p",
                    "-r",
                    str(FPS),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-an",
                    *_BITEXACT,
                    f"cut{i}.mp4",
                ],  # fmt: skip
                workdir,
            )

        mp4 = concat_cuts(
            workdir, cut_count=len(cut_pngs), wavs=wavs, total=total,
            width=spec.width, accent=spec.accent, bar_h=bar_h,
            ffmpeg=ffmpeg, bgm=bgm, bgm_ext=bgm_ext,
        )  # fmt: skip

    return VideoRender(mp4=mp4, duration_s=total, cut_durations_s=tuple(durations))
