"""영상 템플릿 합성 (FR-M2): `VideoSpec` → 쇼츠/릴스 mp4 바이트.

**슬라이드 1장 = 컷 1개 = 화면 1장.** 컷의 표시 시간 = 그 컷 나레이션의 TTS 길이라,
오디오와 화면이 구조적으로 동기화된다.

3단 레이아웃 (검은 바탕):

       0 ~  360   부제 알약(컷마다) + 주제(영상 내내 고정)
     360 ~ 1300   정사각 940 — 코드 이미지, 없으면 그라데이션
    1300 ~ 1920   자막 = 나레이션. 쇼츠 UI 가림 영역을 피해 위쪽부터

2-패스: 컷당 정지 영상 → concat + 진행바 오버레이 + 오디오.

**Ken Burns 줌을 걷어냈다.** 3단 레이아웃에서 화면 전체를 줌하면 주제와 자막까지
확대·크롭된다. 줌이 빠지면서 오버스캔·세그먼트 등분·ASS 자막이 전부 불필요해졌다
(자막이 컷 단위라 PNG에 직접 그리면 된다). 화면 변화는 컷 전환이 담당한다 —
부제·코드 초점·자막이 동시에 바뀐다. 초점 이동은 줌보다 나은 변화다: 의미 없는 움직임
대신 "지금 이 줄을 말하고 있다"는 정보를 담는다.

모든 ffmpeg 호출은 `-bitexact`·단일 스레드 — 같은 입력(같은 TTS 바이트) → 같은 mp4.
렌더러 결정 근거: docs/spikes/c4-renderer-spike.md (ffmpeg+ASS 채택 → ASS는 이후 제거).
"""

import io
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from sns.render.code_image import render_code_square
from sns.render.fonts import FONT_CANDIDATES, pick_font
from sns.render.text import wrap_balanced
from sns.render.video.quality import MAX_DURATION_S
from sns.render.video.spec import Slide, VideoSpec, VideoSpecError
from sns.render.video.tts import Synthesize, wav_duration_s

# 쇼츠 최대 길이(초)는 품질 게이트와 **같은 상수**를 쓴다([sns.render.video.quality]).
# 예전엔 여기에 180.0을 따로 두어, 한쪽만 고치면 렌더는 통과시키고 게이트가 떨어뜨리는
# 조합이 만들어질 수 있었다.
__all__ = ["MAX_DURATION_S", "VideoRender", "render_video"]
FPS = 30

# 3단 비율 (1080×1920 실측 기준). 다른 해상도에서도 같은 구성이 되도록 비율로 둔다.
_TOP_RATIO = 360 / 1920  # 상단 밴드 높이
_SQUARE_RATIO = 940 / 1080  # 정사각 변 / 가로
_TOPIC_DIV = 13.5  # 주제 글자 = 가로 ÷ 계수 (1080 → 80px)
_SUBTITLE_DIV = 28.4  # 부제 알약 (1080 → 38px)
_CAPTION_DIV = 20.0  # 하단 자막 (1080 → 54px)
_PILL_PAD_X_DIV = 31.8
_PILL_PAD_Y_DIV = 60.0
_LINE_SPACING = 1.2
_MARGIN_RATIO = 0.065
_GROUND = (0, 0, 0)
# 진행바 높이(px, 1080 기준 비율로 환산).
_BAR_RATIO = 12 / 1920
# 폰트 후보는 카드와 공유한다([sns.render.fonts]) — 테스트가 monkeypatch로 비우는 지점.
_FONT_CANDIDATES = FONT_CANDIDATES


@dataclass(frozen=True)
class VideoRender:
    mp4: bytes
    duration_s: float
    # 컷(=슬라이드=화면) 단위 길이. 합이 곧 오디오 길이다.
    cut_durations_s: tuple[float, ...]


class VideoRenderError(RuntimeError):
    """ffmpeg 합성 실패."""


def _pick_font(font_path: str | None) -> tuple[str, str]:
    """공용 폰트 해석에 위임 — 카드와 같은 규칙·같은 예외([sns.render.fonts])."""
    return pick_font(font_path, _FONT_CANDIDATES)


@lru_cache(maxsize=32)
def _font(size: int, font_path: str) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path, size)


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


def _wrap(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int
) -> list[str]:
    """균형 줄바꿈 — 알고리즘은 [sns.render.text.wrap_balanced]."""

    def measure(s: str) -> float:
        left, _, right, _ = draw.textbbox((0, 0), s, font=font)
        return right - left

    return wrap_balanced(text, measure, max_w)


def _draw_centered(draw: ImageDraw.ImageDraw, lines: list[str], font: ImageFont.FreeTypeFont,
                   width: int, y: int, line_h: int, fill: tuple[int, int, int]) -> int:  # fmt: skip
    for line in lines:
        left, _, right, _ = draw.textbbox((0, 0), line, font=font)
        draw.text(((width - (right - left)) // 2, y), line, font=font, fill=fill)
        y += line_h
    return y


def _gradient(width: int, height: int, top: str, bottom: str) -> Image.Image:
    """세로 선형 그라데이션 — 코드가 없는 컷의 정사각을 채운다."""
    t, b = _hex_to_rgb(top), _hex_to_rgb(bottom)
    column = Image.new("RGB", (1, height))
    for y in range(height):
        f = y / max(height - 1, 1)
        column.putpixel((0, y), tuple(round(t[c] + (b[c] - t[c]) * f) for c in range(3)))
    return column.resize((width, height))


def _square(slide: Slide, side: int, spec: VideoSpec, mono_path: str | None,
            font_path: str) -> Image.Image:  # fmt: skip
    if slide.code.strip():
        png = render_code_square(
            slide.code, lang=slide.lang or None, size=side,
            focus_lines=slide.focus_lines, mono_path=mono_path, font_path=font_path,
        )  # fmt: skip
        return Image.open(io.BytesIO(png)).convert("RGB")
    return _gradient(side, side, spec.background, spec.background2)


def _frame_png(slide: Slide, spec: VideoSpec, font_path: str, mono_path: str | None) -> bytes:
    """컷 1장 — 알약·주제·정사각·자막을 검은 바탕에 그린다."""
    width, height = spec.width, spec.height
    top_h = round(height * _TOP_RATIO)
    side = round(width * _SQUARE_RATIO)
    margin = round(width * _MARGIN_RATIO)
    fg, accent = _hex_to_rgb(spec.foreground), _hex_to_rgb(spec.accent)

    canvas = Image.new("RGB", (width, height), _GROUND)
    canvas.paste(_square(slide, side, spec, mono_path, font_path), ((width - side) // 2, top_h))
    draw = ImageDraw.Draw(canvas)

    topic_font = _font(round(width / _TOPIC_DIV), font_path)
    sub_font = _font(round(width / _SUBTITLE_DIV), font_path)
    topic_lines = _wrap(draw, spec.topic, topic_font, width - margin * 2)
    topic_line_h = round(topic_font.size * _LINE_SPACING)

    # 알약: 글자 실측 bbox로 크기를 잡아 상하좌우 여백을 같게 한다. 고정 높이에
    # y+오프셋으로 그리면 글꼴 어센더 때문에 글자가 위로 붙는다.
    bx0, by0, bx1, by1 = draw.textbbox((0, 0), slide.subtitle, font=sub_font)
    pad_x, pad_y = round(width / _PILL_PAD_X_DIV), round(width / _PILL_PAD_Y_DIV)
    # textbbox는 float을 돌려준다 — 좌표 계산이 float로 번지지 않게 여기서 정수로 고정.
    pill_w, pill_h = round(bx1 - bx0) + pad_x * 2, round(by1 - by0) + pad_y * 2
    gap = round(top_h * 0.055)

    y = (top_h - (pill_h + gap + topic_line_h * len(topic_lines))) // 2
    px = (width - pill_w) // 2
    draw.rounded_rectangle((px, y, px + pill_w, y + pill_h), radius=pill_h // 2, fill=accent)
    draw.text((px + pad_x - bx0, y + pad_y - by0), slide.subtitle, font=sub_font, fill=_GROUND)

    _draw_centered(draw, topic_lines, topic_font, width, y + pill_h + gap, topic_line_h, fg)

    cap_font = _font(round(width / _CAPTION_DIV), font_path)
    cap_lines = _wrap(draw, slide.narration, cap_font, width - margin * 2)
    _draw_centered(
        draw, cap_lines, cap_font, width, top_h + side + round(height * 0.035),
        round(cap_font.size * 1.33), fg,
    )  # fmt: skip

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=False, compress_level=6)
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


def render_video(
    spec: VideoSpec,
    *,
    synthesize: Synthesize,
    font_path: str | None = None,
    mono_path: str | None = None,
    ffmpeg: str = "ffmpeg",
    bgm: bytes | None = None,
    bgm_ext: str = "mp3",
) -> VideoRender:
    """`VideoSpec` → mp4. 컷당 TTS 1회, WAV 길이가 곧 그 화면의 표시 시간."""
    wavs = [synthesize(s.narration, voice=spec.voice) for s in spec.slides]
    durations = [wav_duration_s(w) for w in wavs]
    total = sum(durations)
    if not 0.0 < total <= MAX_DURATION_S:
        raise VideoSpecError(f"총 길이 {total:.1f}s — 쇼츠 규격(0~{MAX_DURATION_S:.0f}s) 위반")

    resolved_font, _ = _pick_font(font_path)
    bar_h = max(round(spec.height * _BAR_RATIO), 4)

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)

        # 1패스: 컷당 정지 영상. 줌이 없으므로 오버스캔도 세그먼트 등분도 없다.
        for i, (slide, duration) in enumerate(zip(spec.slides, durations, strict=True)):
            (workdir / f"f{i}.png").write_bytes(_frame_png(slide, spec, resolved_font, mono_path))
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

        # 2패스: concat + 진행바 + 오디오. 목록은 반드시 1패스가 만든 컷 수와 같아야 한다
        # — 적게 쓰면 영상만 조용히 잘리고 오디오는 그대로라 뒷부분이 정지 화면이 된다.
        (workdir / "list.txt").write_text(
            "".join(f"file 'cut{i}.mp4'\n" for i in range(len(spec.slides))), encoding="utf-8"
        )
        (workdir / "audio.wav").write_bytes(_concat_wavs(wavs))

        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", "list.txt",
            "-i", "audio.wav",
            "-f", "lavfi", "-t", f"{total:.3f}",
            "-i", f"color=c=0x{spec.accent[1:]}:s={spec.width}x{bar_h}:r={FPS}",
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
        mp4 = (workdir / "out.mp4").read_bytes()

    return VideoRender(mp4=mp4, duration_s=total, cut_durations_s=tuple(durations))
