"""모션 그래픽 템플릿 — 같은 spec, 다른 화면 문법. 실제 mp4를 ffprobe·픽셀로 검증."""

import hashlib
import shutil
from collections.abc import Mapping

import pytest

from sns.render.storage import InMemoryMediaStore
from sns.render.video.media import VideoRenderMedia
from sns.render.video.motion import render_motion_video
from sns.render.video.quality import check_video
from sns.render.video.spec import VideoSpec, VideoSpecError
from sns.render.video.spec import parse_video_spec as _parse_video_spec
from sns.topic_policy import DEV_MAJOR
from tests.test_video_render import _frame_at, _solid_png, tone_wav

# 이 파일의 픽스처는 요리 주제다 — 화면 문법을 보지 주제 분기를 보지 않으므로 고정한다.
COOK_MAJOR = "요리"


def parse_video_spec(media_spec: Mapping[str, object]) -> VideoSpec:
    return _parse_video_spec(media_spec, topic_major=COOK_MAJOR)


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe 필요 — CI에서 설치·실행",
)

MOTION_DICT: dict[str, object] = {
    "topic": "초간편 아침 베이킹",
    "style": "motion",
    "slides": [
        {"subtitle": "재료 두 가지", "narration": "식빵이랑 계란만 있으면 됩니다."},
        {"subtitle": "에어프라이어", "narration": "십 분이면 완성이에요."},
    ],
}


def test_style_parsed_and_validated() -> None:
    assert parse_video_spec(MOTION_DICT).style == "motion"
    assert parse_video_spec({**MOTION_DICT, "style": ""}).style == ""
    with pytest.raises(VideoSpecError, match="style"):
        parse_video_spec({**MOTION_DICT, "style": "vaporwave"})


def test_motion_render_passes_quality_gate() -> None:
    render = render_motion_video(parse_video_spec(MOTION_DICT), synthesize=tone_wav)
    report = check_video(render.mp4)
    assert report.passed, report.failures
    assert len(render.cut_durations_s) == 2


def test_motion_render_deterministic() -> None:
    spec = parse_video_spec(MOTION_DICT)
    a = render_motion_video(spec, synthesize=tone_wav)
    b = render_motion_video(spec, synthesize=tone_wav)
    assert hashlib.sha256(a.mp4).digest() == hashlib.sha256(b.mp4).digest()


def test_motion_demotes_code_and_concept_to_gradient() -> None:
    """코드·개념그림 컷은 거부하지 않고 그라데이션으로 강등 — 거부하면 사이클이 수시로
    죽는다(실측: 요리 채널 첫 사이클부터 concept와 코드 비유가 나왔다).

    **개발 대분류로 파싱한다.** 코드를 안 쓰는 주제에서는 파서가 한 층 앞에서 거부하므로
    (정사각 소스 목록, [sns.topic_policy]) 강등이 성립하는 건 코드를 쓰는 주제뿐이다.
    """
    spec = _parse_video_spec(
        topic_major=DEV_MAJOR,
        media_spec={
            **MOTION_DICT,
            "slides": [
                {"subtitle": "코드 컷", "narration": "한 문장.", "code": "print(1)"},
                {
                    "subtitle": "핵심 비교",
                    "narration": "개념 그림 컷도 렌더돼야 합니다.",
                    "concept": {"kind": "emphasis", "headline": "10분", "tag": "완성"},
                },
            ],
        },
    )
    render = render_motion_video(spec, synthesize=tone_wav)
    assert check_video(render.mp4).passed


def test_motion_character_bounces() -> None:
    """캐릭터가 컷 안에서 실제로 움직인다 — 같은 픽셀이 시점에 따라 달라야 한다."""
    lime = (60, 220, 60)
    spec = parse_video_spec({**MOTION_DICT, "character_ref": "mem://image/char.png"})
    render = render_motion_video(
        spec, synthesize=tone_wav, fetch_image=lambda ref: _solid_png(lime, side=300)
    )
    # 배지(원형) 중심축의 하단 경계 픽셀: 바운스가 0인 시점(t≈0.81, sin(t·3.9)≈0)에는
    # 배지 안, 최고점(t=0.4, lift 24px)에는 배경이다.
    margin = round(spec.width * 0.065)
    size = round(spec.width * 0.20)
    bar_h = max(round(spec.height * 12 / 1920), 4)
    x = spec.width - margin - size // 2
    y = spec.height - margin - bar_h - 14  # 정지 시 배지 하단에서 14px 안쪽
    rest = _frame_at(render.mp4, 0.81).getpixel((x, y))
    lifted = _frame_at(render.mp4, 0.40).getpixel((x, y))
    # 라임 판정: 초록이 지배(G 높고 R 낮음). 배경 팔레트가 바뀌어도 흔들리지 않는다.
    assert rest[1] > 150 and rest[0] < 120, f"정지 시점에 배지가 없음: {rest}"
    assert not (lifted[1] > 150 and lifted[0] < 120), (
        f"최고점에도 배지가 그대로 — 움직임 없음: {lifted}"
    )


def test_media_binding_dispatches_by_style() -> None:
    """같은 렌더 바인딩이 spec의 style로 템플릿을 고른다 — 재렌더 배선의 근거."""
    render_media = VideoRenderMedia(
        InMemoryMediaStore(), synthesize=tone_wav, topic_major=COOK_MAJOR
    )
    asset = render_media(MOTION_DICT, "video")
    assert asset.kind == "video" and asset.storage_url.endswith(".mp4")
