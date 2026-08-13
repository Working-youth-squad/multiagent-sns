"""영상 템플릿 합성 (C4) — `media_spec` → 쇼츠/릴스 mp4 `MediaAsset` (FR-M2·M3)."""

from sns.render.video.media import VideoRenderMedia
from sns.render.video.quality import VideoQualityReport, check_video
from sns.render.video.renderer import VideoRender, VideoRenderError, render_video
from sns.render.video.spec import VideoSpec, VideoSpecError, parse_video_spec
from sns.render.video.tts import Synthesize, synthesize_google

__all__ = [
    "Synthesize",
    "VideoQualityReport",
    "VideoRender",
    "VideoRenderError",
    "VideoRenderMedia",
    "VideoSpec",
    "VideoSpecError",
    "check_video",
    "parse_video_spec",
    "render_video",
    "synthesize_google",
]
