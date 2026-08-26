"""생성 클립 템플릿(style="clip") — 실제 mp4를 ffprobe·픽셀로 검증 (motion 테스트 동형)."""

import shutil
import subprocess
from collections.abc import Mapping

import pytest

from sns.render.storage import InMemoryMediaStore
from sns.render.video.clip import render_clip_video
from sns.render.video.media import VideoRenderMedia
from sns.render.video.quality import check_video
from sns.render.video.spec import VideoSpec, VideoSpecError
from sns.render.video.spec import parse_video_spec as _parse_video_spec
from tests.test_video_render import _frame_at, tone_wav

# 픽스처는 요리 주제 — 화면 문법을 보지 주제 분기를 보지 않으므로 고정한다.
COOK_MAJOR = "요리"


def parse_video_spec(media_spec: Mapping[str, object]) -> VideoSpec:
    return _parse_video_spec(media_spec, topic_major=COOK_MAJOR)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe 필요 — CI에서 설치·실행",
)

CLIP_DICT: dict[str, object] = {
    "topic": "생성 클립 데모",
    "style": "clip",
    "slides": [
        {"subtitle": "갓 구운 빵", "narration": "오븐에서 갓 나온 빵이에요."},
        {"subtitle": "바삭한 단면", "narration": "단면이 이렇게 바삭합니다."},
    ],
}


def _lime_clip(seconds: float = 1.0) -> bytes:
    """단색(라임) 테스트 클립 — Veo 산출물 자리의 결정론 대역."""
    out = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x3CDC3C:s=320x568:d={seconds}:r=30",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-f",
            "mp4",
            "-movflags",
            "frag_keyframe+empty_moov",
            "-",
        ],  # fmt: skip
        capture_output=True,
        check=True,
    )
    return out.stdout


def test_clip_style_parsed_and_registered() -> None:
    spec = parse_video_spec({**CLIP_DICT})
    assert spec.style == "clip"
    slides = [{**s, "clip_ref": "mem://video/c.mp4"} for s in CLIP_DICT["slides"]]  # type: ignore[union-attr]
    assert parse_video_spec({**CLIP_DICT, "slides": slides}).slides[0].clip_ref


def test_clip_render_uses_clip_as_background() -> None:
    """clip_ref 배경이 실제 프레임에 깔린다 — 커버 크롭 후 화면 어디나 라임이어야 한다."""
    clip = _lime_clip()
    slides = [{**s, "clip_ref": "mem://video/c.mp4"} for s in CLIP_DICT["slides"]]  # type: ignore[union-attr]
    spec = parse_video_spec({**CLIP_DICT, "slides": slides})
    render = render_clip_video(spec, synthesize=tone_wav, fetch_image=lambda ref: clip)
    assert check_video(render.mp4).passed
    pixel = _frame_at(render.mp4, 0.3).getpixel((spec.width // 2, round(spec.height * 0.3)))
    assert pixel[1] > 150 and pixel[0] < 130, f"클립 배경이 아님: {pixel}"


def test_clip_render_falls_back_to_still_without_clip_ref() -> None:
    """클립이 없으면(생성 실패 컷) 정지 배경 폴백 — 영상은 그대로 나온다."""
    render = render_clip_video(parse_video_spec(CLIP_DICT), synthesize=tone_wav)
    assert check_video(render.mp4).passed
    assert len(render.cut_durations_s) == 2


def test_clip_ref_without_fetch_seam_raises() -> None:
    slides = [{**s, "clip_ref": "mem://video/c.mp4"} for s in CLIP_DICT["slides"]]  # type: ignore[union-attr]
    spec = parse_video_spec({**CLIP_DICT, "slides": slides})
    with pytest.raises(VideoSpecError, match="fetch_image"):
        render_clip_video(spec, synthesize=tone_wav)


def test_media_binding_dispatches_clip_style() -> None:
    render_media = VideoRenderMedia(
        InMemoryMediaStore(), synthesize=tone_wav, topic_major=COOK_MAJOR
    )
    asset = render_media(CLIP_DICT, "video")
    assert asset.kind == "video" and asset.storage_url
