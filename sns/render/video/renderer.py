"""영상 템플릿 합성 (FR-M2): `VideoSpec` → 쇼츠/릴스 mp4 바이트.

2-패스 구성 (품질 개선판):
1. 장당 세그먼트 — 그라데이션+타이포 계층 슬라이드 PNG(1.3배 오버스캔)를
   Ken Burns 줌(짝수 장 줌인/홀수 장 줌아웃 — 2~4s 화면 변화, FR-A2)으로 영상화.
2. 최종 합성 — 세그먼트 concat + ASS 자막(나레이션) + 하단 진행바 + 오디오
   (나레이션 WAV, 선택적 BGM 저음량 루프 믹스).

각 슬라이드의 표시 시간 = 그 슬라이드 나레이션 WAV 길이 — 오디오와 화면 전환이
구조적으로 동기화된다. 모든 ffmpeg 호출은 `-bitexact`·단일 스레드 — 같은 입력
(같은 TTS 바이트) → 같은 mp4 바이트 (c4-renderer 스파이크에서 검증한 조건).

렌더러 결정 근거: docs/spikes/c4-renderer-spike.md (ffmpeg+ASS 채택).
"""

import io
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from sns.render.video.spec import Slide, VideoSpec, VideoSpecError
from sns.render.video.subtitles import build_ass
from sns.render.video.tts import Synthesize, wav_duration_s

# 쇼츠 최대 길이(초) — 13-로드맵 §5 외부 제약.
MAX_DURATION_S = 180.0
FPS = 30
# Ken Burns 최대 줌 배율과 오버스캔(줌해도 선명하도록 원본을 크게 렌더).
_ZOOM_MAX = 1.12
_OVERSCAN = 1.3
# (Pillow용 폰트 파일, ASS/fontconfig용 패밀리 이름) — 앞에서부터 존재하는 것 사용.
_FONT_CANDIDATES = (
    (r"C:\Windows\Fonts\malgunbd.ttf", "Malgun Gothic"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "Noto Sans CJK KR"),
)
# 타이포 계층: 제목 = 가로 ÷ 10, 본문 = 가로 ÷ 22. 안전영역 = 가로의 8.3% 여백.
_TITLE_DIV = 10
_BODY_DIV = 22
_MARGIN_RATIO = 0.083
_LINE_SPACING = 1.25
# 진행바 높이(px, 1080 기준 비율로 환산).
_BAR_RATIO = 12 / 1920


@dataclass(frozen=True)
class VideoRender:
    mp4: bytes
    duration_s: float
    # 컷(=나레이션 문장) 단위 길이. 슬라이드 하나가 여러 컷을 낳을 수 있다.
    cut_durations_s: tuple[float, ...]


class VideoRenderError(RuntimeError):
    """ffmpeg 합성 실패."""


def _pick_font(font_path: str | None) -> tuple[str | None, str]:
    """(Pillow 폰트 경로, ASS 패밀리 이름). 명시 경로가 오면 이름은 파일 stem."""
    if font_path is not None:
        return font_path, Path(font_path).stem
    for path, family in _FONT_CANDIDATES:
        if Path(path).exists():
            return path, family
    return None, "sans-serif"  # Pillow 내장 폰트 + 시스템 기본 — 한글 폰트 없는 환경 폴백


_Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


@lru_cache(maxsize=16)
def _font(size: int, font_path: str | None) -> _Font:
    if font_path is not None:
        return ImageFont.truetype(font_path, size)
    return ImageFont.load_default(size=size)


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: _Font, max_width: int) -> list[str]:
    """공백 기준 줄바꿈, 토큰이 폭을 넘으면 글자 단위 분할(한글 대응).

    C3 카드 renderer의 _wrap과 동일 알고리즘 — 카드 머지 후 공용 유틸로 승격 예정.
    """

    def width_of(s: str) -> float:
        left, _, right, _ = draw.textbbox((0, 0), s, font=font)
        return right - left

    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for token in paragraph.split(" "):
            if not token:
                continue
            trial = token if not current else f"{current} {token}"
            if width_of(trial) <= max_width:
                current = trial
                continue
            if current:
                lines.append(current)
                current = ""
            if width_of(token) <= max_width:
                current = token
            else:
                buf = ""
                for ch in token:
                    if buf and width_of(buf + ch) > max_width:
                        lines.append(buf)
                        buf = ch
                    else:
                        buf += ch
                current = buf
        lines.append(current)
    return lines


def _gradient(width: int, height: int, top: str, bottom: str) -> Image.Image:
    """세로 선형 그라데이션 — 1px 세로줄을 만들어 가로로 늘린다(빠르고 결정론)."""
    t, b = _hex_to_rgb(top), _hex_to_rgb(bottom)
    column = Image.new("RGB", (1, height))
    for y in range(height):
        f = y / max(height - 1, 1)
        column.putpixel((0, y), tuple(round(t[c] + (b[c] - t[c]) * f) for c in range(3)))
    return column.resize((width, height))


def _slide_png(slide: Slide, spec: VideoSpec, font_path: str | None) -> bytes:
    """슬라이드 1장 — 그라데이션 배경 + 액센트 바 + 제목(대)/본문(소) 계층.

    Ken Burns 줌을 위해 목표 해상도의 _OVERSCAN 배로 렌더한다(줌인해도 선명).
    """
    width = round(spec.width * _OVERSCAN / 2) * 2  # 짝수 강제 (yuv420p)
    height = round(spec.height * _OVERSCAN / 2) * 2
    img = _gradient(width, height, spec.background, spec.background2)
    draw = ImageDraw.Draw(img)
    fg = _hex_to_rgb(spec.foreground)
    accent = _hex_to_rgb(spec.accent)
    margin = round(width * _MARGIN_RATIO)
    max_text_width = width - margin * 2

    title_size = width // _TITLE_DIV
    body_size = width // _BODY_DIV
    title_font = _font(title_size, font_path)
    body_font = _font(body_size, font_path)
    title_lines = _wrap(draw, slide.title, title_font, max_text_width)
    body_lines = _wrap(draw, slide.body, body_font, max_text_width) if slide.body else []
    title_h = round(title_size * _LINE_SPACING)
    body_h = round(body_size * _LINE_SPACING)
    gap = round(height * 0.03)
    bar_h = round(height * 0.006)

    block_h = bar_h + gap + title_h * len(title_lines)
    if body_lines:
        block_h += gap + body_h * len(body_lines)
    # 블록을 화면 세로 중앙보다 살짝 위에 — 하단 자막(나레이션)과 분리.
    y = (height - block_h) // 2 - round(height * 0.05)

    # 액센트 바 (제목 위 장식)
    bar_w = round(width * 0.12)
    draw.rectangle(((width - bar_w) // 2, y, (width + bar_w) // 2, y + bar_h), fill=accent)
    y += bar_h + gap
    for line in title_lines:
        left, _, right, _ = draw.textbbox((0, 0), line, font=title_font)
        draw.text(((width - (right - left)) // 2, y), line, font=title_font, fill=fg)
        y += title_h
    if body_lines:
        y += gap
        dim = tuple(round(c * 0.72) for c in fg)  # 본문은 살짝 낮은 대비 — 계층감
        for line in body_lines:
            left, _, right, _ = draw.textbbox((0, 0), line, font=body_font)
            draw.text(((width - (right - left)) // 2, y), line, font=body_font, fill=dim)
            y += body_h

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def _concat_wavs(wavs: list[bytes]) -> bytes:
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


_BITEXACT = (
    "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
    "-bitexact", "-map_metadata", "-1", "-threads", "1",
)  # fmt: skip


def _run_ffmpeg(cmd: list[str], workdir: Path) -> None:
    result = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    if result.returncode != 0:
        raise VideoRenderError(f"ffmpeg 실패(exit {result.returncode}): {result.stderr.strip()}")


def _zoom_expr(index: int, frames: int) -> str:
    """짝수 컷 줌인, 홀수 컷 줌아웃 — 컷별 교차로 단조로움 방지 (결정론)."""
    span = _ZOOM_MAX - 1.0
    if index % 2 == 0:
        return f"1+{span:.4f}*on/{max(frames - 1, 1)}"
    return f"{_ZOOM_MAX:.4f}-{span:.4f}*on/{max(frames - 1, 1)}"


def render_video(
    spec: VideoSpec,
    *,
    synthesize: Synthesize,
    font_path: str | None = None,
    ffmpeg: str = "ffmpeg",
    bgm: bytes | None = None,
    bgm_ext: str = "mp3",
) -> VideoRender:
    """`VideoSpec` → mp4. 장당 TTS 1회, WAV 길이가 곧 슬라이드 타이밍.

    bgm: 선택적 배경음악 바이트(플랫폼 중립 소스 — 06 §4, 에셋 조달은 후속).
    나레이션 우선 믹스(BGM 12% 볼륨), 총 길이는 나레이션 기준.
    """
    # 컷 = 나레이션 문장 1개. 슬라이드가 아니라 컷이 TTS·세그먼트·자막의 단위다 —
    # 나레이션이 길어도 문장마다 화면이 바뀌어 정지 구간이 4초를 넘지 않는다(FR-A2).
    cuts = spec.cuts
    wavs = [synthesize(c.text, voice=spec.voice) for c in cuts]
    durations = [wav_duration_s(w) for w in wavs]
    total = sum(durations)
    if not 0.0 < total <= MAX_DURATION_S:
        raise VideoSpecError(f"총 길이 {total:.1f}s — 쇼츠 규격(0~{MAX_DURATION_S:.0f}s) 위반")

    pillow_font, ass_font = _pick_font(font_path)
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)

        # 1패스: 컷당 Ken Burns 세그먼트 (줌 방향이 컷마다 교차 → 문장마다 화면 변화)
        for i, cut in enumerate(cuts):
            (workdir / f"slide{i}.png").write_bytes(_slide_png(cut.slide, spec, pillow_font))
            frames = max(round(durations[i] * FPS), 1)
            zoompan = (
                f"zoompan=z='{_zoom_expr(i, frames)}'"
                f":x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'"
                f":d={frames}:s={spec.width}x{spec.height}:fps={FPS}"
            )
            _run_ffmpeg(
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    f"slide{i}.png",
                    "-vf",
                    f"{zoompan},format=yuv420p",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-an",
                    *_BITEXACT,
                    f"seg{i}.mp4",
                ],  # fmt: skip
                workdir,
            )

        # 2패스: concat + 자막(나레이션) + 진행바 + 오디오
        (workdir / "list.txt").write_text(
            "".join(f"file 'seg{i}.mp4'\n" for i in range(len(spec.slides))), encoding="utf-8"
        )
        (workdir / "audio.wav").write_bytes(_concat_wavs(wavs))
        (workdir / "subs.ass").write_text(
            build_ass(
                [c.text for c in cuts],
                durations,
                width=spec.width,
                height=spec.height,
                font=ass_font,
            ),
            encoding="utf-8",
        )
        bar_h = max(round(spec.height * _BAR_RATIO), 4)
        bar_color = f"0x{spec.accent[1:]}"
        video_chain = (
            f"subtitles=subs.ass,"
            f"drawbox=x=0:y=ih-{bar_h}:w='iw*t/{total:.3f}':h={bar_h}"
            f":color={bar_color}@0.85:t=fill,"
            f"format=yuv420p"
        )
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", "list.txt",
            "-i", "audio.wav",
        ]  # fmt: skip
        if bgm is not None:
            (workdir / f"bgm.{bgm_ext}").write_bytes(bgm)
            cmd += ["-stream_loop", "-1", "-i", f"bgm.{bgm_ext}"]
            cmd += [
                "-filter_complex",
                f"[0:v]{video_chain}[v];"
                "[1:a]volume=1.0[nar];[2:a]volume=0.12[bg];"
                "[nar][bg]amix=inputs=2:duration=first:normalize=0[a]",
                "-map", "[v]", "-map", "[a]",
            ]  # fmt: skip
        else:
            cmd += ["-vf", video_chain]
        cmd += [
            "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-t", f"{total:.3f}",
            *_BITEXACT,
            "out.mp4",
        ]  # fmt: skip
        _run_ffmpeg(cmd, workdir)
        mp4 = (workdir / "out.mp4").read_bytes()
    return VideoRender(mp4=mp4, duration_s=total, cut_durations_s=tuple(durations))
