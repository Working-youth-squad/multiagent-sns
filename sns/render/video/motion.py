"""모션 그래픽 영상 템플릿 — 글 최소화, 풀블리드 배경 + 키워드 타이포 + 모션.

3단 레이아웃과 **같은 spec**([sns.render.video.spec])을 다른 화면 문법으로 그린다.
spec에 `style: "motion"`이면 [sns.render.video.media]가 이쪽으로 디스패치한다.

화면 문법 (1080×1920):
  - 배경: 해소된 사진/생성 이미지를 풀블리드 크롭, 없으면 그라데이션. 느린 줌(zoompan)
  - 텍스트: 하단 자막(나레이션) **하나뿐** — 주제·키워드까지 세 군데 얹었더니 화면이
    글로 덮였다. 내용은 나레이션·음성이, 화면은 이미지가 맡는다
  - 캐릭터(`character_ref`): 우하단에서 바운스 — 결제를 켜고 장면 생성이 붙으면
    배경 자체가 캐릭터 장면이 된다(B 확장: 코드 변경 없이 이미지 소스만 바뀐다)
  - 키워드는 페이드+라이즈로 등장 — 전부 ffmpeg 표현식이라 추가 의존성 0

**컷 전환은 하드컷.** xfade는 컷을 겹치며 전체 길이를 줄여, WAV 이어붙이기로 만드는
오디오와 길이가 어긋난다(컷 길이 = TTS 길이 동기 구조가 깨진다). 쇼츠 문법에서
하드컷은 표준이고, 움직임은 컷 안(줌·라이즈·바운스)이 담당한다.

**코드·개념그림 컷은 그라데이션 배경으로 강등한다(거부 아님).** 이 화면 문법은
풀블리드 이미지가 전제라 정사각 코드/도해를 앉힐 자리가 없다. 그렇다고 거부하면
사이클이 수시로 죽는다 — 실측: 요리 채널 첫 사이클부터 concept가 나왔고, 코드 비유
(bread = '식빵')까지 나왔다. 내용 전달은 키워드 타이포+나레이션이 하고 있으므로
정사각 소스는 배경에서 빠져도 성립한다. 애초에 안 나오게 하는 건 스타일 지침
(scripts/run_profile_cycle.py의 playbook_guidance) 몫이다.
"""

import io
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from sns.render.video.quality import MAX_DURATION_S
from sns.render.video.renderer import (
    _BAR_RATIO,
    _BITEXACT,
    FPS,
    FetchImage,
    VideoRender,
    _assemble,
    _character_badge,
    _font,
    _gradient,
    _pick_font,
    _run_ffmpeg,
    _wrap,
)
from sns.render.video.spec import Slide, VideoSpec, VideoSpecError
from sns.render.video.tts import Synthesize, wav_duration_s

__all__ = ["render_motion_video"]

# 글자는 하단 자막 **하나뿐**이다 — 주제 라벨·키워드 타이포까지 세 군데에 얹었더니
# 화면이 글로 덮였다(실사용 피드백). 내용 전달은 나레이션이, 화면은 이미지가 맡는다.
_CAPTION_DIV = 16.0  # 하단 자막 (1080 → 67px) — 유일한 글자라 잘 보이게 키운다
_MARGIN_RATIO = 0.065
_CAPTION_Y = 0.76  # 쇼츠 하단 UI(진행바·버튼) 가림 영역 위
# 자막 뒤 반투명 밴드 — 그림자 1획으로는 밝은 사진 위에서 글자가 묻혔다(실사용 피드백).
# 밴드 위 글자는 배경·테마와 무관하게 항상 흰색이다.
_BAND_ALPHA = 150
_BAND_PAD_X = 0.45  # 폰트 크기 대비 좌우 패딩
_BAND_PAD_Y = 0.26  # 폰트 크기 대비 상하 패딩
_BADGE_RATIO = 0.20  # 모션 템플릿의 캐릭터는 배지가 아니라 출연자라 3단(0.16)보다 크다
# 모션 파라미터 — 전부 ffmpeg 표현식 상수.
_ZOOM_AMOUNT = 0.08  # 컷 동안 1.0 → 1.08
_RISE_PX = 40  # 키워드 라이즈 거리
_RISE_S = 0.4
_BOUNCE_PX = 24
_BOUNCE_HZ = 3.9  # abs(sin(t*ω)) — 초당 약 1.2회 바운스


def _cover(img: Image.Image, width: int, height: int) -> Image.Image:
    """가운데 기준 커버 크롭 — 저장된 정사각(940)을 9:16 풀블리드로 채운다."""
    scale = max(width / img.width, height / img.height)
    resized = img.resize(
        (round(img.width * scale), round(img.height * scale)), Image.Resampling.LANCZOS
    )
    x = (resized.width - width) // 2
    y = (resized.height - height) // 2
    return resized.crop((x, y, x + width, y + height))


def _bg_png(slide: Slide, spec: VideoSpec, fetch_image: FetchImage | None) -> bytes:
    if slide.image_ref:
        if fetch_image is None:
            raise VideoSpecError(
                f"'image_ref'({slide.image_ref})가 있는데 fetch_image seam이 없음 — "
                "조용히 그라데이션으로 떨어지지 않는다"
            )
        photo = Image.open(io.BytesIO(fetch_image(slide.image_ref))).convert("RGB")
        canvas = _cover(photo, spec.width, spec.height)
    else:
        canvas = _gradient(spec.width, spec.height, spec.background, spec.background2)
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def _text_png(slide: Slide, spec: VideoSpec, font_path: str) -> bytes:
    """투명 텍스트 레이어 — 하단 자막(나레이션) 한 덩어리뿐이다.

    subtitle·topic은 그리지 않는다: spec에는 남아 있지만(3단 템플릿·승인 웹 편집이
    쓴다) 이 화면 문법에서는 자막 하나가 가독성이 가장 좋았다. 줄마다 반투명 밴드를
    깔고 흰 글자를 얹는다 — 배경이 어떤 사진이든 읽힌다.
    """
    width, height = spec.width, spec.height
    margin = round(width * _MARGIN_RATIO)
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    cap_font = _font(round(width / _CAPTION_DIV), font_path)
    pad_x = round(cap_font.size * _BAND_PAD_X)
    pad_y = round(cap_font.size * _BAND_PAD_Y)
    y = round(height * _CAPTION_Y)
    for line in _wrap(draw, slide.narration, cap_font, width - margin * 2 - pad_x * 2):
        left, _, right, _ = draw.textbbox((0, 0), line, font=cap_font)
        text_w = round(right - left)
        x = (width - text_w) // 2
        draw.rounded_rectangle(
            (x - pad_x, y - pad_y, x + text_w + pad_x, y + cap_font.size + pad_y),
            radius=round(cap_font.size * 0.3),
            fill=(0, 0, 0, _BAND_ALPHA),
        )
        draw.text((x, y), line, font=cap_font, fill=(255, 255, 255, 255))
        y += cap_font.size + pad_y * 2 + round(cap_font.size * 0.16)

    buf = io.BytesIO()
    layer.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def _cut_cmd(index: int, duration: float, spec: VideoSpec, *, ffmpeg: str,
             has_character: bool, bar_h: int) -> list[str]:  # fmt: skip
    """컷 1개의 애니메이션 합성 — 줌(배경)·라이즈+페이드(텍스트)·바운스(캐릭터)."""
    frames = max(round(duration * FPS), 1)
    w, h = spec.width, spec.height
    zoom = (
        f"[0:v]zoompan=z='1+{_ZOOM_AMOUNT}*on/{frames}':d={frames}"
        f":x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':s={w}x{h}:fps={FPS}[bg];"
    )
    rise = (
        "[1:v]format=rgba,fade=t=in:st=0:d="
        f"{_RISE_S}:alpha=1[tx];"
        f"[bg][tx]overlay=x=0:y='{_RISE_PX}*max(0,({_RISE_S}-t)/{_RISE_S})'"
    )
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", f"bg{index}.png",
        "-loop", "1", "-t", f"{duration:.3f}", "-i", f"tx{index}.png",
    ]  # fmt: skip
    if has_character:
        margin = round(w * _MARGIN_RATIO)
        base_y = f"{h}-h-{margin + bar_h}"
        chain = (
            zoom + rise + "[v1];"
            f"[v1][2:v]overlay=x={w}-w-{margin}"
            f":y='{base_y}-{_BOUNCE_PX}*abs(sin(t*{_BOUNCE_HZ}))'[v2];"
            "[v2]format=yuv420p[v]"
        )
        cmd += ["-loop", "1", "-t", f"{duration:.3f}", "-i", f"ch{index}.png"]
    else:
        chain = zoom + rise + ",format=yuv420p[v]"
    cmd += [
        "-filter_complex", chain, "-map", "[v]",
        "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
        "-an", "-t", f"{duration:.3f}", *_BITEXACT, f"cut{index}.mp4",
    ]  # fmt: skip
    return cmd


def render_motion_video(
    spec: VideoSpec,
    *,
    synthesize: Synthesize,
    font_path: str | None = None,
    mono_path: str | None = None,  # 시그니처 호환용 — 코드 컷이 없어 쓰지 않는다
    fetch_image: FetchImage | None = None,
    ffmpeg: str = "ffmpeg",
    bgm: bytes | None = None,
    bgm_ext: str = "mp3",
) -> VideoRender:
    """`VideoSpec(style="motion")` → mp4. 컷 길이 = TTS 길이는 3단과 동일."""
    wavs = [synthesize(s.narration, voice=spec.voice) for s in spec.slides]
    durations = [wav_duration_s(w) for w in wavs]
    total = sum(durations)
    if not 0.0 < total <= MAX_DURATION_S:
        raise VideoSpecError(f"총 길이 {total:.1f}s — 쇼츠 규격(0~{MAX_DURATION_S:.0f}s) 위반")

    resolved_font, _ = _pick_font(font_path)
    bar_h = max(round(spec.height * _BAR_RATIO), 4)

    character_png: bytes | None = None
    if spec.character_ref:
        if fetch_image is None:
            raise VideoSpecError(
                f"'character_ref'({spec.character_ref})가 있는데 fetch_image seam이 없음 — "
                "조용히 캐릭터 없이 렌더하지 않는다"
            )
        badge = _character_badge(fetch_image(spec.character_ref), round(spec.width * _BADGE_RATIO))
        buf = io.BytesIO()
        badge.save(buf, format="PNG", optimize=False, compress_level=6)
        character_png = buf.getvalue()

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for i, (slide, duration) in enumerate(zip(spec.slides, durations, strict=True)):
            (workdir / f"bg{i}.png").write_bytes(_bg_png(slide, spec, fetch_image))
            (workdir / f"tx{i}.png").write_bytes(_text_png(slide, spec, resolved_font))
            if character_png is not None:
                (workdir / f"ch{i}.png").write_bytes(character_png)
            _run_ffmpeg(
                _cut_cmd(
                    i,
                    duration,
                    spec,
                    ffmpeg=ffmpeg,
                    has_character=character_png is not None,
                    bar_h=bar_h,
                ),  # fmt: skip
                workdir,
            )
        mp4 = _assemble(
            workdir, cut_count=len(spec.slides), wavs=wavs, total=total,
            width=spec.width, accent=spec.accent, bar_h=bar_h,
            ffmpeg=ffmpeg, bgm=bgm, bgm_ext=bgm_ext,
        )  # fmt: skip

    return VideoRender(mp4=mp4, duration_s=total, cut_durations_s=tuple(durations))
