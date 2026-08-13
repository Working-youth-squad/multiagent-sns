"""영상 템플릿 합성 (FR-M2): `VideoSpec` → 쇼츠/릴스 mp4 바이트.

슬라이드 PNG(Pillow) + 장당 TTS WAV + ASS 자막을 ffmpeg 1회 호출로 합성한다.
각 슬라이드의 표시 시간 = 그 슬라이드 나레이션 WAV 길이 — 오디오와 화면 전환이
구조적으로 동기화된다. ffmpeg는 `-bitexact`·단일 스레드로 돌려 같은 입력(같은
TTS 바이트) → 같은 mp4 바이트를 보장한다(c4-renderer 스파이크에서 검증).

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

from sns.render.video.spec import VideoSpec, VideoSpecError
from sns.render.video.subtitles import build_ass
from sns.render.video.tts import Synthesize, wav_duration_s

# 쇼츠 최대 길이(초) — 13-로드맵 §5 외부 제약.
MAX_DURATION_S = 180.0
FPS = 30
# (Pillow용 폰트 파일, ASS/fontconfig용 패밀리 이름) — 앞에서부터 존재하는 것 사용.
_FONT_CANDIDATES = (
    (r"C:\Windows\Fonts\malgunbd.ttf", "Malgun Gothic"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "Noto Sans CJK KR"),
)
# 화면 텍스트 크기 = 가로 ÷ 계수, 안전영역 = 가로의 8.3% 여백 (C3 카드와 동일 규격).
_TEXT_DIV = 12
_MARGIN_RATIO = 0.083
_LINE_SPACING = 1.25


@dataclass(frozen=True)
class VideoRender:
    mp4: bytes
    duration_s: float
    slide_durations_s: tuple[float, ...]


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


def _slide_png(text: str, spec: VideoSpec, font_path: str | None) -> bytes:
    """슬라이드 1장 — 화면 중앙(살짝 위)에 텍스트 스택."""
    img = Image.new("RGB", (spec.width, spec.height), _hex_to_rgb(spec.background))
    draw = ImageDraw.Draw(img)
    size = spec.width // _TEXT_DIV
    font = _font(size, font_path)
    margin = round(spec.width * _MARGIN_RATIO)
    lines = _wrap(draw, text, font, spec.width - margin * 2)
    line_h = round(size * _LINE_SPACING)
    # 세로 중앙에서 자막 공간만큼 위로 — 자막(하단)과 겹치지 않게.
    y = (spec.height - line_h * len(lines)) // 2 - round(spec.height * 0.06)
    for line in lines:
        left, _, right, _ = draw.textbbox((0, 0), line, font=font)
        draw.text(
            ((spec.width - (right - left)) // 2, y),
            line,
            font=font,
            fill=_hex_to_rgb(spec.foreground),
        )
        y += line_h
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


def render_video(
    spec: VideoSpec,
    *,
    synthesize: Synthesize,
    font_path: str | None = None,
    ffmpeg: str = "ffmpeg",
) -> VideoRender:
    """`VideoSpec` → mp4. 장당 TTS 1회, WAV 길이가 곧 슬라이드 타이밍."""
    wavs = [synthesize(text, voice=spec.voice) for text in spec.slides]
    durations = [wav_duration_s(w) for w in wavs]
    total = sum(durations)
    if not 0.0 < total <= MAX_DURATION_S:
        raise VideoSpecError(f"총 길이 {total:.1f}s — 쇼츠 규격(0~{MAX_DURATION_S:.0f}s) 위반")

    pillow_font, ass_font = _pick_font(font_path)
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        concat_lines = []
        for i, text in enumerate(spec.slides):
            (workdir / f"slide{i}.png").write_bytes(_slide_png(text, spec, pillow_font))
            concat_lines.append(f"file 'slide{i}.png'\nduration {durations[i]}\n")
        concat_lines.append(f"file 'slide{len(spec.slides) - 1}.png'\n")  # concat demuxer 규칙
        (workdir / "list.txt").write_text("".join(concat_lines), encoding="utf-8")
        (workdir / "audio.wav").write_bytes(_concat_wavs(wavs))
        (workdir / "subs.ass").write_text(
            build_ass(spec.slides, durations, width=spec.width, height=spec.height, font=ass_font),
            encoding="utf-8",
        )
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
            "-i",
            "audio.wav",
            "-vf",
            "subtitles=subs.ass,format=yuv420p",
            "-r",
            str(FPS),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-t",
            f"{total:.3f}",
            # 결정론(FR-M1): bitexact 3종 + muxer bitexact + 단일 스레드 (스파이크 검증).
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
        result = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
        if result.returncode != 0:
            raise VideoRenderError(
                f"ffmpeg 실패(exit {result.returncode}): {result.stderr.strip()}"
            )
        mp4 = (workdir / "out.mp4").read_bytes()
    return VideoRender(mp4=mp4, duration_s=total, slide_durations_s=tuple(durations))
