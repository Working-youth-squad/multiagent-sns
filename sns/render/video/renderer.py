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
from math import ceil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from sns.render.text import wrap_balanced
from sns.render.video.spec import Cut, Slide, VideoSpec, VideoSpecError
from sns.render.video.subtitles import build_ass
from sns.render.video.tts import Synthesize, wav_duration_s

# 쇼츠 최대 길이(초) — 13-로드맵 §5 외부 제약.
MAX_DURATION_S = 180.0
# 화면 세그먼트 1개의 최대 길이 — FR-A2가 요구하는 화면 전환 주기 2~4초의 상한.
# 컷 오디오가 이보다 길면 **화면만** 등분해 세그먼트를 늘린다(오디오·자막 불변).
# 스펙의 문장 폭 상한으로는 보장이 안 된다: TTS 발화 길이는 같은 문장도 호출마다
# 다르고(비결정론), 숫자·기호는 폭 대비 2배 넘게 걸린다. 실측 WAV 길이만이 근거다.
MAX_SEGMENT_S = 4.0
FPS = 30
# Ken Burns 최대 줌 배율과 오버스캔(줌해도 선명하도록 원본을 크게 렌더).
_ZOOM_MAX = 1.12
_OVERSCAN = 1.3
# (Pillow용 폰트 파일, ASS/fontconfig용 패밀리 이름) — 앞에서부터 존재하는 것 사용.
# 배포 타깃은 Linux/Docker이므로 Noto CJK를 우선한다(Dockerfile이 fonts-noto-cjk 설치).
# Windows 경로는 로컬 개발 편의용 폴백 — 프로덕션 렌더는 여기에 의존하지 않는다.
_FONT_CANDIDATES = (
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "Noto Sans CJK KR"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "Noto Sans CJK KR"),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "Noto Sans CJK KR"),
    (r"C:\Windows\Fonts\malgunbd.ttf", "Malgun Gothic"),
)
# 타이포 계층: 제목 = 가로 ÷ 10, 본문 = 가로 ÷ 22. 안전영역 = 가로의 8.3% 여백.
_TITLE_DIV = 10
_BODY_DIV = 22
_MARGIN_RATIO = 0.083
_LINE_SPACING = 1.25
# 진행바 높이(px, 1080 기준 비율로 환산).
_BAR_RATIO = 12 / 1920
# 액센트 바 — PNG가 아니라 **필터 체인**에서 그린다. PNG에 박으면 Ken Burns 줌에
# 휩쓸려 흘러다닌다. 화면 좌표에 고정해 제목 블록의 시선 기준선 역할만 한다.
_ACCENT_Y_RATIO = 0.30
_ACCENT_W_RATIO = 0.12
_ACCENT_H_RATIO = 10 / 1920
# 제목 블록 상단 위치(PNG 세로 비율) — 액센트 바 바로 아래.
_TITLE_TOP_RATIO = 0.335


@dataclass(frozen=True)
class VideoRender:
    mp4: bytes
    duration_s: float
    # 컷(=나레이션 문장) 단위 길이. 슬라이드 하나가 여러 컷을 낳을 수 있다.
    cut_durations_s: tuple[float, ...]
    # 화면 세그먼트 길이 — 전부 MAX_SEGMENT_S 이하. 컷 하나가 여러 세그먼트를 낳을 수 있다.
    segment_durations_s: tuple[float, ...] = ()


class VideoRenderError(RuntimeError):
    """ffmpeg 합성 실패."""


class FontNotFoundError(VideoRenderError):
    """한글 렌더 가능한 폰트를 못 찾음 — 두부(□) 렌더로 이어지지 않도록 명시적 실패."""


def _pick_font(font_path: str | None) -> tuple[str, str]:
    """(Pillow 폰트 경로, ASS 패밀리 이름). 명시 경로가 오면 이름은 파일 stem.

    CJK 폰트가 하나도 없으면 조용히 내장 폰트로 폴백(=한글 두부)하지 않고 raise한다 —
    깨진 자막이 발행 파이프라인을 통과하는 것을 원천 차단(FR-Q1).
    """
    if font_path is not None:
        return font_path, Path(font_path).stem
    for path, family in _FONT_CANDIDATES:
        if Path(path).exists():
            return path, family
    raise FontNotFoundError(
        "한글(CJK) 폰트를 찾을 수 없습니다 — 컨테이너에 fonts-noto-cjk를 설치하거나 "
        "font_path를 명시하세요. 탐색 경로: " + ", ".join(p for p, _ in _FONT_CANDIDATES)
    )


_Font = ImageFont.FreeTypeFont


@lru_cache(maxsize=16)
def _font(size: int, font_path: str) -> _Font:
    return ImageFont.truetype(font_path, size)


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: _Font, max_width: int) -> list[str]:
    """균형 줄바꿈 — 줄 수는 그대로 두고 어절이 자연스러운 자리에서 갈리게 한다.

    알고리즘은 [sns.render.text.wrap_balanced], 여기선 Pillow 폰트 메트릭만 주입한다.
    """

    def measure(s: str) -> float:
        left, _, right, _ = draw.textbbox((0, 0), s, font=font)
        return right - left

    return wrap_balanced(text, measure, max_width)


def _gradient(width: int, height: int, top: str, bottom: str) -> Image.Image:
    """세로 선형 그라데이션 — 1px 세로줄을 만들어 가로로 늘린다(빠르고 결정론)."""
    t, b = _hex_to_rgb(top), _hex_to_rgb(bottom)
    column = Image.new("RGB", (1, height))
    for y in range(height):
        f = y / max(height - 1, 1)
        column.putpixel((0, y), tuple(round(t[c] + (b[c] - t[c]) * f) for c in range(3)))
    return column.resize((width, height))


def _slide_png(slide: Slide, spec: VideoSpec, font_path: str) -> bytes:
    """슬라이드 1장 — 그라데이션 배경 + 제목(대)/본문(소) 계층.

    액센트 바는 여기 없다 — 필터 체인이 화면 좌표에 그린다(_accent_filter).

    Ken Burns 줌을 위해 목표 해상도의 _OVERSCAN 배로 렌더한다(줌인해도 선명).
    """
    width = round(spec.width * _OVERSCAN / 2) * 2  # 짝수 강제 (yuv420p)
    height = round(spec.height * _OVERSCAN / 2) * 2
    img = _gradient(width, height, spec.background, spec.background2)
    draw = ImageDraw.Draw(img)
    fg = _hex_to_rgb(spec.foreground)
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

    # 제목 블록은 **고정 상단**에서 시작한다(액센트 바 바로 아래). 예전처럼 블록 높이로
    # 세로 중앙을 잡으면 제목 줄 수가 달라질 때마다 화면 전체가 위아래로 흔들렸다.
    y = round(height * _TITLE_TOP_RATIO)
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


def _accent_filter(spec: VideoSpec) -> str:
    """제목 위 액센트 바 — 화면 좌표에 **고정**된 기준선.

    PNG에 그리면 Ken Burns 줌에 휩쓸려 흘러다니므로 필터 체인에서 그린다.
    컷 전환마다 길이를 펄스시켜 봤지만, 화면 폭의 12%짜리 요소가 0.3초 동안 조금
    늘어나는 건 전환 신호로 읽히지 않고 잔떨림으로만 보였다 — 고정이 낫다.
    """
    width = max(round(spec.width * _ACCENT_W_RATIO), 8)
    height = max(round(spec.height * _ACCENT_H_RATIO), 4)
    return (
        f"drawbox=x={(spec.width - width) // 2}:y={round(spec.height * _ACCENT_Y_RATIO)}"
        f":w={width}:h={height}:color=0x{spec.accent[1:]}:t=fill"
    )


def _segments(cuts: "tuple[Cut, ...]", durations: "list[float]") -> list[tuple["Cut", float]]:
    """컷을 MAX_SEGMENT_S 이하 세그먼트로 등분한다 — 오디오는 건드리지 않는다.

    등분이라 세그먼트 길이 합 = 컷 길이 합 = 오디오 길이. 자막 타이밍(컷 기준)과
    화면 전환(세그먼트 기준)이 어긋나지 않는다.
    """
    out: list[tuple[Cut, float]] = []
    for cut, duration in zip(cuts, durations, strict=True):
        count = max(ceil(duration / MAX_SEGMENT_S), 1)
        out.extend([(cut, duration / count)] * count)
    return out


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

        # 1패스: 세그먼트당 Ken Burns. 세그먼트 = 컷을 MAX_SEGMENT_S 이하로 등분한 것 —
        # 줌 방향이 세그먼트마다 교차하므로 긴 나레이션 중에도 화면이 4초마다 뒤집힌다.
        segments = _segments(cuts, durations)
        for i, (cut, seg_duration) in enumerate(segments):
            (workdir / f"slide{i}.png").write_bytes(_slide_png(cut.slide, spec, pillow_font))
            frames = max(round(seg_duration * FPS), 1)
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
            # 1패스가 만든 **세그먼트 수**와 반드시 같아야 한다. 적게 쓰면 영상만 조용히
            # 잘리고(오디오는 그대로) 뒷부분이 정지 화면으로 재생된다.
            "".join(f"file 'seg{i}.mp4'\n" for i in range(len(segments))),
            encoding="utf-8",
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
        # 진행바: 화면 폭짜리 색 소스를 왼쪽 밖(-W)에서 0까지 밀어넣어 "차오르게" 한다.
        # drawbox로는 안 된다 — drawbox 표현식의 `t`는 타임스탬프가 아니라 **선 두께**라
        # 매 프레임 같은 값이 나와 바가 처음부터 꽉 찬 채로 멈춘다. overlay의 `x`는 `t`가
        # 타임스탬프라 시간에 따라 실제로 움직인다.
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", "list.txt",
            "-i", "audio.wav",
            "-f", "lavfi", "-t", f"{total:.3f}",
            "-i", f"color=c={bar_color}:s={spec.width}x{bar_h}:r={FPS}",
        ]  # fmt: skip
        video_chain = (
            f"[0:v]subtitles=subs.ass,{_accent_filter(spec)}[base];"
            f"[base][2:v]overlay=x='-W+W*t/{total:.3f}':y=H-{bar_h}:shortest=1,"
            f"format=yuv420p[v]"
        )
        if bgm is not None:
            (workdir / f"bgm.{bgm_ext}").write_bytes(bgm)
            cmd += ["-stream_loop", "-1", "-i", f"bgm.{bgm_ext}"]
            cmd += [
                "-filter_complex",
                f"{video_chain};"
                "[1:a]volume=1.0[nar];[3:a]volume=0.12[bg];"
                "[nar][bg]amix=inputs=2:duration=first:normalize=0[a]",
                "-map", "[v]", "-map", "[a]",
            ]  # fmt: skip
        else:
            cmd += ["-filter_complex", video_chain, "-map", "[v]", "-map", "1:a"]
        cmd += [
            "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-t", f"{total:.3f}",
            *_BITEXACT,
            "out.mp4",
        ]  # fmt: skip
        _run_ffmpeg(cmd, workdir)
        mp4 = (workdir / "out.mp4").read_bytes()
    return VideoRender(
        mp4=mp4,
        duration_s=total,
        cut_durations_s=tuple(durations),
        segment_durations_s=tuple(d for _, d in segments),
    )
