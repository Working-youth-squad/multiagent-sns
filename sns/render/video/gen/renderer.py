"""생성 장면 렌더 — 풀블리드 장면 위에 주제·자막을 굽는다.

3단 레이아웃(검은 밴드 / 정사각 / 검은 밴드)과 다르다. 장면이 화면 전체를 채우고,
글자는 그 위에 얹힌다:

    0 ~  360   위→아래로 옅어지는 검은 마스크 + 주제·알약
  360 ~ 1300   장면(가림 없음)
 1300 ~ 1920   아래→위로 옅어지는 검은 마스크 + 자막

**마스크가 검은 밴드를 대신한다.** 생성 이미지 위의 글자는 대비가 보장되지 않는다 —
밝은 장면이 오면 흰 자막이 사라진다. 3단 레이아웃은 밴드를 아예 검게 칠해 그 문제를
피했는데, 풀블리드에서는 그럴 수 없으니 그라데이션 마스크를 깐다.

컷 재료(PNG)만 만들고 조립은 [sns.render.video.assemble]이 한다 — 템플릿 트랙과 같은
조립기를 쓰므로 진행바·`-bitexact`·오디오 처리가 한 벌이다.
"""

import io

from PIL import Image, ImageDraw

from sns.render.video.assemble import VideoRender, assemble_video
from sns.render.video.quality import MAX_DURATION_S
from sns.render.video.renderer import (
    _CAPTION_DIV,
    _GROUND,
    _LINE_SPACING,
    _MARGIN_RATIO,
    _PILL_PAD_X_DIV,
    _PILL_PAD_Y_DIV,
    _SUBTITLE_DIV,
    _TOP_RATIO,
    _TOPIC_DIV,
    FetchImage,
    _draw_centered,
    _font,
    _gradient,
    _hex_to_rgb,
    _pick_font,
    _wrap,
)
from sns.render.video.spec import Slide, VideoSpec, VideoSpecError
from sns.render.video.tts import Synthesize, wav_duration_s

# 실패 컷 비율 상한. 절반이 빈 화면이면 "무리한 저품질 영상 발행 금지"(06 §5)에 걸린다.
# 사이클 하나를 버리는 게 그런 영상이 채널에 남는 것보다 싸다 — 사람이 안 보는 경로다.
MAX_FAILED_SCENE_RATIO = 0.5

# 마스크가 완전히 불투명해지는 지점의 알파. 글자를 읽히게 하되 장면을 통째로 덮지 않는다.
_MASK_ALPHA = 220
# 하단 마스크 높이 비율 — 자막 밴드(1300~)보다 조금 위에서 시작해 경계가 안 보이게 한다.
_BOTTOM_MASK_RATIO = 0.36


def _scene_background(slide: Slide, spec: VideoSpec, fetch_image: FetchImage | None) -> Image.Image:
    """장면 이미지를 화면 크기로. 실패가 기록된 컷은 그라데이션으로 간다."""
    if not slide.scene_ref or fetch_image is None:
        return _gradient(spec.width, spec.height, spec.background, spec.background2)
    scene = Image.open(io.BytesIO(fetch_image(slide.scene_ref))).convert("RGB")
    if scene.size == (spec.width, spec.height):
        return scene
    # 비율이 다르면 채우고 가운데를 남긴다 — 레터박스를 두면 풀블리드가 아니게 된다.
    scale = max(spec.width / scene.width, spec.height / scene.height)
    resized = scene.resize(
        (
            max(round(scene.width * scale), spec.width),
            max(round(scene.height * scale), spec.height),
        ),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - spec.width) // 2
    top = (resized.height - spec.height) // 2
    return resized.crop((left, top, left + spec.width, top + spec.height))


def _apply_mask(canvas: Image.Image, height: int, *, from_top: bool) -> None:
    """가장자리에서 안쪽으로 옅어지는 검은 마스크 — 글자 대비를 보장한다."""
    width = canvas.width
    alpha = Image.new("L", (1, height))
    for y in range(height):
        # from_top이면 위가 짙고 아래로 갈수록 투명해진다.
        ratio = 1.0 - (y / max(height - 1, 1)) if from_top else y / max(height - 1, 1)
        alpha.putpixel((0, y), round(_MASK_ALPHA * ratio))
    mask = alpha.resize((width, height))
    shade = Image.new("RGB", (width, height), _GROUND)
    box = (0, 0) if from_top else (0, canvas.height - height)
    region = canvas.crop((box[0], box[1], width, box[1] + height))
    canvas.paste(Image.composite(shade, region, mask), box)


def _scene_frame_png(
    slide: Slide, spec: VideoSpec, font_path: str, fetch_image: FetchImage | None
) -> bytes:
    """컷 1장 — 풀블리드 장면 + 마스크 + 주제·알약·자막."""
    width, height = spec.width, spec.height
    top_h = round(height * _TOP_RATIO)
    bottom_h = round(height * _BOTTOM_MASK_RATIO)
    margin = round(width * _MARGIN_RATIO)
    fg, accent = _hex_to_rgb(spec.foreground), _hex_to_rgb(spec.accent)

    canvas = _scene_background(slide, spec, fetch_image)
    _apply_mask(canvas, top_h, from_top=True)
    _apply_mask(canvas, bottom_h, from_top=False)
    draw = ImageDraw.Draw(canvas)

    topic_font = _font(round(width / _TOPIC_DIV), font_path)
    sub_font = _font(round(width / _SUBTITLE_DIV), font_path)
    topic_lines = _wrap(draw, spec.topic, topic_font, width - margin * 2)
    topic_line_h = round(topic_font.size * _LINE_SPACING)

    bx0, by0, bx1, by1 = draw.textbbox((0, 0), slide.subtitle, font=sub_font)
    pad_x, pad_y = round(width / _PILL_PAD_X_DIV), round(width / _PILL_PAD_Y_DIV)
    pill_w, pill_h = round(bx1 - bx0) + pad_x * 2, round(by1 - by0) + pad_y * 2
    gap = round(top_h * 0.055)

    y = (top_h - (pill_h + gap + topic_line_h * len(topic_lines))) // 2
    px = (width - pill_w) // 2
    draw.rounded_rectangle((px, y, px + pill_w, y + pill_h), radius=pill_h // 2, fill=accent)
    draw.text((px + pad_x - bx0, y + pad_y - by0), slide.subtitle, font=sub_font, fill=_GROUND)
    _draw_centered(draw, topic_lines, topic_font, width, y + pill_h + gap, topic_line_h, fg)

    cap_font = _font(round(width / _CAPTION_DIV), font_path)
    cap_lines = _wrap(draw, slide.narration, cap_font, width - margin * 2)
    cap_line_h = round(cap_font.size * 1.33)
    # 자막은 하단 마스크 안에서 위쪽부터 — 쇼츠 UI 가림(하단 300px)을 피한다.
    cap_top = height - bottom_h + round(height * 0.02)
    _draw_centered(draw, cap_lines, cap_font, width, cap_top, cap_line_h, fg)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def render_scene_video(
    spec: VideoSpec,
    *,
    synthesize: Synthesize,
    fetch_image: FetchImage | None = None,
    font_path: str | None = None,
    ffmpeg: str = "ffmpeg",
) -> VideoRender:
    """`VideoSpec`(generated_scene) → mp4. 컷당 TTS 1회, WAV 길이가 표시 시간이다."""
    missing = sum(1 for s in spec.slides if not s.scene_ref)
    if missing and missing / len(spec.slides) >= MAX_FAILED_SCENE_RATIO:
        raise VideoSpecError(
            f"장면 생성 실패 {missing}/{len(spec.slides)}컷 — "
            "빈 화면이 절반 이상이라 렌더하지 않는다(06 §5 무리한 저품질 발행 금지)"
        )

    wavs = [synthesize(s.narration, voice=spec.voice) for s in spec.slides]
    durations = [wav_duration_s(w) for w in wavs]
    total = sum(durations)
    if not 0.0 < total <= MAX_DURATION_S:
        raise VideoSpecError(f"총 길이 {total:.1f}s — 쇼츠 규격(0~{MAX_DURATION_S:.0f}s) 위반")

    resolved_font, _ = _pick_font(font_path)
    cut_pngs = [_scene_frame_png(slide, spec, resolved_font, fetch_image) for slide in spec.slides]
    return assemble_video(cut_pngs, durations, wavs, spec=spec, ffmpeg=ffmpeg)
