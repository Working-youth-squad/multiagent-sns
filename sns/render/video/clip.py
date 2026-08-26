"""생성 클립 영상 템플릿 (`style="clip"`) — 컷 배경이 정지 이미지가 아니라 **영상**이다.

팀 4방향 중 ① "영상 클립 자체 생성". [sns.render.clips.generate]가 만든 클립
(slide.clip_ref)을 풀블리드 배경으로 깔고, 하단 자막 하나만 얹는다 — 글자 규율은
motion 템플릿과 같다(자막 = 나레이션, 주제·키워드 없음).

motion과 같은 spec·같은 조립(_assemble: concat + 진행바 + 오디오)을 쓰고 배경만
다르다: 클립은 TTS 길이만큼 앞에서 자른다(Veo 8초 > 컷 2~4초 — 남는 쪽을 버리는
방향이라 안전하고, 혹시 짧아도 -stream_loop로 돈다). `clip_ref`가 없는 컷은
이미지/그라데이션 정지 화면으로 폴백한다 — 클립 생성 실패가 영상을 죽이지 않는다.

기존 영상 제작 트랙과 **별개 기능**이다: 온보딩 사이클은 이 스타일을 모른다.
독립 진입점은 scripts/preview_clip_video.py — spec만 있으면 여기 단독으로 돈다.
"""

import tempfile
from pathlib import Path

from sns.render.video.assemble import (
    _BAR_RATIO,
    _BITEXACT,
    FPS,
    VideoRender,
    _run_ffmpeg,
    concat_cuts,
)
from sns.render.video.motion import _bg_png, _text_png
from sns.render.video.quality import MAX_DURATION_S
from sns.render.video.renderer import FetchImage, _pick_font
from sns.render.video.spec import VideoSpec, VideoSpecError
from sns.render.video.tts import Synthesize, wav_duration_s

__all__ = ["render_clip_video"]


def _cut_cmd(index: int, duration: float, spec: VideoSpec, *, ffmpeg: str,
             has_clip: bool) -> list[str]:  # fmt: skip
    """컷 1개 — 클립이면 커버 크롭 + 트림, 아니면 정지 배경. 자막 레이어는 공통."""
    w, h = spec.width, spec.height
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    if has_clip:
        # 클립이 컷보다 짧아도 끊기지 않게 루프 — -t가 실제 길이를 자른다.
        cmd += ["-stream_loop", "-1", "-t", f"{duration:.3f}", "-i", f"clip{index}.mp4"]
        bg = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={FPS}[bg];"
    else:
        cmd += ["-loop", "1", "-t", f"{duration:.3f}", "-i", f"bg{index}.png"]
        bg = f"[0:v]fps={FPS}[bg];"
    cmd += [
        "-loop", "1", "-t", f"{duration:.3f}", "-i", f"tx{index}.png",
        "-filter_complex", bg + "[1:v]format=rgba[tx];[bg][tx]overlay=0:0,format=yuv420p[v]",
        "-map", "[v]", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
        "-an", "-t", f"{duration:.3f}", *_BITEXACT, f"cut{index}.mp4",
    ]  # fmt: skip
    return cmd


def render_clip_video(
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
    """`VideoSpec(style="clip")` → mp4. 컷 길이 = TTS 길이는 다른 템플릿과 동일."""
    wavs = [synthesize(s.narration, voice=spec.voice) for s in spec.slides]
    durations = [wav_duration_s(w) for w in wavs]
    total = sum(durations)
    if not 0.0 < total <= MAX_DURATION_S:
        raise VideoSpecError(f"총 길이 {total:.1f}s — 쇼츠 규격(0~{MAX_DURATION_S:.0f}s) 위반")

    resolved_font, _ = _pick_font(font_path)
    bar_h = max(round(spec.height * _BAR_RATIO), 4)

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for i, (slide, duration) in enumerate(zip(spec.slides, durations, strict=True)):
            has_clip = bool(slide.clip_ref)
            if has_clip:
                if fetch_image is None:
                    raise VideoSpecError(
                        f"'clip_ref'({slide.clip_ref})가 있는데 fetch_image seam이 없음 — "
                        "조용히 정지 화면으로 떨어지지 않는다"
                    )
                (workdir / f"clip{i}.mp4").write_bytes(fetch_image(slide.clip_ref))
            else:
                (workdir / f"bg{i}.png").write_bytes(_bg_png(slide, spec, fetch_image))
            (workdir / f"tx{i}.png").write_bytes(_text_png(slide, spec, resolved_font))
            _run_ffmpeg(_cut_cmd(i, duration, spec, ffmpeg=ffmpeg, has_clip=has_clip), workdir)
        mp4 = concat_cuts(
            workdir, cut_count=len(spec.slides), wavs=wavs, total=total,
            width=spec.width, accent=spec.accent, bar_h=bar_h,
            ffmpeg=ffmpeg, bgm=bgm, bgm_ext=bgm_ext,
        )  # fmt: skip

    return VideoRender(mp4=mp4, duration_s=total, cut_durations_s=tuple(durations))
