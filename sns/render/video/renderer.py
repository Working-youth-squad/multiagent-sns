"""영상 템플릿 합성 (FR-M2): `VideoSpec` → 쇼츠/릴스 mp4 바이트.

**슬라이드 1장 = 컷 1개 = 화면 1장.** 컷의 표시 시간 = 그 컷 나레이션의 TTS 길이라,
오디오와 화면이 구조적으로 동기화된다.

3단 레이아웃 (검은 바탕):

       0 ~  360   부제 알약(컷마다) + 주제(영상 내내 고정)
     360 ~ 1300   정사각 940 — 코드 → 개념 그림 → 주제 사진 → 그라데이션 순
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
from collections.abc import Callable
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from sns.render.concept_image import render_concept_square
from sns.render.fonts import FONT_CANDIDATES, pick_font
from sns.render.text import wrap_balanced
from sns.render.video.assemble import (
    VideoRender,
    VideoRenderError,
    assemble_video,
)
from sns.render.video.mascot import place_mascot
from sns.render.video.quality import MAX_DURATION_S
from sns.render.video.spec import Slide, VideoSpec, VideoSpecError
from sns.render.video.tts import Synthesize, wav_duration_s

# 쇼츠 최대 길이(초)는 품질 게이트와 **같은 상수**를 쓴다([sns.render.video.quality]).
# 예전엔 여기에 180.0을 따로 두어, 한쪽만 고치면 렌더는 통과시키고 게이트가 떨어뜨리는
# 조합이 만들어질 수 있었다.
# VideoRender·VideoRenderError는 [sns.render.video.assemble]로 옮겼지만 여기서 재수출한다
# — 기존 import 경로(from sns.render.video.renderer import VideoRenderError)가 살아 있어야
# 호출부·테스트가 안 깨진다.
__all__ = ["MAX_DURATION_S", "VideoRender", "VideoRenderError", "assemble_video", "render_video"]

# 저장된 사진 바이트를 읽는 seam(FR-M3). 렌더러는 저장소 구현을 모른다 — `synthesize`와 같은 규율.
FetchImage = Callable[[str], bytes]

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
# 폰트 후보는 카드와 공유한다([sns.render.fonts]) — 테스트가 monkeypatch로 비우는 지점.
_FONT_CANDIDATES = FONT_CANDIDATES


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
            font_path: str, fetch_image: FetchImage | None) -> Image.Image:  # fmt: skip
    """가운데 칸 — **팩이 정한 순서**대로 첫 번째로 채워지는 소스를 쓴다(`spec.square_sources`).

    개발 도메인의 기본 순서는 코드 → 개념 그림 → 주제 사진 → 그라데이션이다. 앞의 셋은
    우리가 그리거나 못박은 것이라 저작권·네트워크 리스크가 없고, 실사 사진이 마지막인 건
    추상 개념을 못 그리기 때문이다 — "list vs set"에 전선 사진이 왔다.

    순서를 팩에서 받는 이유: 코드가 없는 도메인은 그 칸이 아예 없고, 사진이 1순위인
    도메인도 있을 수 있다. 렌더러가 팩을 직접 알지 않도록 파서가 spec에 실어 보낸다.
    """
    for source in spec.square_sources:
        if source == "code" and slide.code.strip():
            # 지연 import — 코드를 쓰지 않는 도메인이 pygments를 물지 않게 한다
            # ([sns.render.video.tts]의 google.cloud 지연 import와 같은 규율).
            from sns.render.code_image import render_code_square

            png = render_code_square(
                slide.code, lang=slide.lang or None, size=side,
                focus_lines=slide.focus_lines, mono_path=mono_path, font_path=font_path,
            )  # fmt: skip
            return Image.open(io.BytesIO(png)).convert("RGB")
        if source == "concept" and slide.concept is not None:
            png = render_concept_square(
                slide.concept, size=side, font_path=font_path, mono_path=mono_path
            )
            return Image.open(io.BytesIO(png)).convert("RGB")
        if source == "image" and slide.image_ref:
            if fetch_image is None:
                raise VideoSpecError(
                    f"'image_ref'({slide.image_ref})가 있는데 fetch_image seam이 없음 — "
                    "조용히 그라데이션으로 떨어지지 않는다"
                )
            photo = Image.open(io.BytesIO(fetch_image(slide.image_ref))).convert("RGB")
            # 저장된 건 이미 정사각이지만, 해상도가 다른 spec에서도 슬롯을 정확히 채우게 맞춘다.
            if photo.size != (side, side):
                photo = photo.resize((side, side), Image.Resampling.LANCZOS)
            return photo
        if source == "gradient":
            break
    # 팩이 gradient를 안 적었어도 빈 칸으로 두지 않는다 — 화면에 구멍이 나는 것보다 낫다.
    return _gradient(side, side, spec.background, spec.background2)


def _character_badge(png: bytes, size: int) -> Image.Image:
    """캐릭터 PNG → 원형 배지. 생성 캐릭터는 단색 배경이라 사각 그대로 붙이면 상자가 된다."""
    src = Image.open(io.BytesIO(png)).convert("RGB").resize((size, size), Image.Resampling.LANCZOS)
    # 안티앨리어싱: 마스크를 4배로 그려서 줄인다 — 원 둘레 계단 현상 방지.
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4, size * 4), fill=255)
    badge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    badge.paste(src, (0, 0), mask.resize((size, size), Image.Resampling.LANCZOS))
    return badge


def _paste_mascot(canvas: Image.Image, slide: Slide, spec: VideoSpec, square_x: int,
                  square_y: int, side: int, fetch_image: FetchImage | None) -> None:  # fmt: skip
    """채널 캐릭터를 컷 성격에 맞는 자리에 **원형 배지**로 얹는다.

    두 갈래가 합쳐진 자리다: 모양은 `_character_badge`(단색 배경이 상자로 보이는 걸
    원형 마스크로 없앤다), 자리는 [sns.render.video.mascot]의 컷 성격별 배치다.
    우하단 고정이 아닌 이유는 정사각 내용이 컷마다 달라서다.

    **코드가 있는 컷은 건너뛴다.** 정사각의 어느 모서리든 코드 줄 위라, 가리면 못 읽는다.
    사진·개념 그림은 가려도 되지만 코드는 정보 밀도가 다르다.

    캐릭터가 없거나(인터뷰에서 "캐릭터 없음") 되읽기가 실패하면 조용히 건너뛴다 —
    사진 해소와 같은 폴백 규율이다. 캐릭터 때문에 영상이 죽지 않는다.
    """
    if not spec.character_ref or slide.code:
        return
    if fetch_image is None:
        # **배선 실수는 조용히 넘기지 않는다.** 아래 폴백은 런타임 실패(깨진 이미지·저장소
        # 오류)의 몫이고, seam이 아예 없는 건 부르는 쪽이 틀린 것이다.
        raise VideoSpecError(
            f"'character_ref'({spec.character_ref})가 있는데 fetch_image seam이 없음 — "
            "조용히 캐릭터 없이 렌더하지 않는다"
        )
    spot = place_mascot(
        slide.concept.kind if slide.concept else None,
        square_x=square_x,
        square_y=square_y,
        side=side,
    )
    try:
        badge = _character_badge(fetch_image(spec.character_ref), spot.size)
    except Exception:
        return  # 저장소 오류·깨진 이미지 — 캐릭터만 빠지고 영상은 나간다
    canvas.paste(badge, (spot.x, spot.y), badge)


def _frame_png(slide: Slide, spec: VideoSpec, font_path: str, mono_path: str | None,
               fetch_image: FetchImage | None) -> bytes:  # fmt: skip
    """컷 1장 — 알약·주제·정사각·자막(+캐릭터 배지)을 검은 바탕에 그린다."""
    width, height = spec.width, spec.height
    top_h = round(height * _TOP_RATIO)
    side = round(width * _SQUARE_RATIO)
    margin = round(width * _MARGIN_RATIO)
    fg, accent = _hex_to_rgb(spec.foreground), _hex_to_rgb(spec.accent)

    canvas = Image.new("RGB", (width, height), _GROUND)
    square_x = (width - side) // 2
    canvas.paste(_square(slide, side, spec, mono_path, font_path, fetch_image), (square_x, top_h))
    _paste_mascot(canvas, slide, spec, square_x, top_h, side, fetch_image)
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


def render_video(
    spec: VideoSpec,
    *,
    synthesize: Synthesize,
    font_path: str | None = None,
    mono_path: str | None = None,
    fetch_image: FetchImage | None = None,
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
    cut_pngs = [
        _frame_png(slide, spec, resolved_font, mono_path, fetch_image) for slide in spec.slides
    ]
    return assemble_video(
        cut_pngs, durations, wavs, spec=spec, ffmpeg=ffmpeg, bgm=bgm, bgm_ext=bgm_ext
    )
