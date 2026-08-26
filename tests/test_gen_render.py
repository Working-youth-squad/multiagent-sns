"""생성 장면 렌더 — 풀블리드 장면 + 어두운 마스크 + 구운 자막.

3단 레이아웃의 검은 밴드가 하던 일(글자 대비 보장)을 마스크가 대신한다. 밝은 장면이
와도 자막이 읽혀야 한다.
"""

import hashlib
import io
import shutil

import pytest
from PIL import Image

from sns.render.video.gen.renderer import render_scene_video
from sns.render.video.quality import check_video
from sns.render.video.spec import VideoSpec, VideoSpecError, parse_video_spec
from tests.test_video_render import _frame_at, tone_wav

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe 필요 — CI에서 설치·실행",
)

_REF = "mem://image/scene.png"


def _scene_png(color: tuple[int, int, int] = (240, 240, 240)) -> bytes:
    """**밝은** 장면 — 마스크가 없으면 흰 자막이 사라지는 최악의 경우다."""
    buf = io.BytesIO()
    Image.new("RGB", (1080, 1920), color).save(buf, format="PNG")
    return buf.getvalue()


def _spec(*slides: dict[str, object]) -> VideoSpec:
    return parse_video_spec(
        {"topic": "자취 요리", "method": "generated_scene", "slides": list(slides)},
        topic_major="요리",
    )


def _cut(n: int, **extra: object) -> dict[str, object]:
    return {
        "subtitle": f"{n}단계",
        "narration": f"{n}번째 문장입니다.",
        "scene_prompt": "a warm kitchen",
        **extra,
    }


def test_scene_video_passes_the_quality_gate() -> None:
    spec = _spec(_cut(1, scene_ref=_REF), _cut(2, scene_ref=_REF))
    render = render_scene_video(spec, synthesize=tone_wav, fetch_image=lambda ref: _scene_png())
    report = check_video(render.mp4)
    assert report.passed, report.failures
    assert len(render.cut_durations_s) == 2


def test_scene_fills_the_frame() -> None:
    """풀블리드 — 3단 레이아웃과 달리 가운데가 정사각 슬롯이 아니다."""
    spec = _spec(_cut(1, scene_ref=_REF), _cut(2, scene_ref=_REF))
    render = render_scene_video(spec, synthesize=tone_wav, fetch_image=lambda ref: _scene_png())
    img = _frame_at(render.mp4, render.duration_s * 0.3)
    # 화면 좌우 끝(정사각이라면 검은 여백인 자리)에 장면이 있어야 한다.
    assert sum(img.getpixel((8, 800))) > 300, "좌측 여백이 비어 있다 — 풀블리드가 아니다"
    assert sum(img.getpixel((1070, 800))) > 300, "우측 여백이 비어 있다"


def test_caption_band_is_darkened() -> None:
    """생성 이미지 위의 글자는 대비가 보장되지 않는다 — 마스크가 깔려야 한다."""
    spec = _spec(_cut(1, scene_ref=_REF), _cut(2, scene_ref=_REF))
    render = render_scene_video(spec, synthesize=tone_wav, fetch_image=lambda ref: _scene_png())
    img = _frame_at(render.mp4, render.duration_s * 0.3)
    middle = sum(img.getpixel((540, 800)))
    bottom = sum(img.getpixel((20, 1900)))
    assert bottom < middle * 0.5, f"하단이 안 어두워졌다 ({bottom} vs {middle})"


def test_topic_band_is_darkened() -> None:
    spec = _spec(_cut(1, scene_ref=_REF), _cut(2, scene_ref=_REF))
    render = render_scene_video(spec, synthesize=tone_wav, fetch_image=lambda ref: _scene_png())
    img = _frame_at(render.mp4, render.duration_s * 0.3)
    assert sum(img.getpixel((20, 10))) < sum(img.getpixel((540, 800))) * 0.5


def test_render_is_deterministic() -> None:
    spec = _spec(_cut(1, scene_ref=_REF), _cut(2, scene_ref=_REF))
    a = render_scene_video(spec, synthesize=tone_wav, fetch_image=lambda ref: _scene_png())
    b = render_scene_video(spec, synthesize=tone_wav, fetch_image=lambda ref: _scene_png())
    assert hashlib.sha256(a.mp4).digest() == hashlib.sha256(b.mp4).digest()


def test_one_failed_cut_falls_back_to_gradient() -> None:
    """실패가 기록된 컷은 scene_ref가 없다 — 그 컷만 그라데이션으로 간다."""
    spec = _spec(
        _cut(1, scene_ref=_REF),
        _cut(2, scene_ref=_REF),
        _cut(3, scene_failure={"kind": "safety"}),
    )
    render = render_scene_video(spec, synthesize=tone_wav, fetch_image=lambda ref: _scene_png())
    assert check_video(render.mp4).passed


def test_too_many_failed_scenes_is_refused() -> None:
    """절반 이상이 그라데이션이면 그 사이클을 버리는 게 싸다 — 무인 발행 경로다."""
    spec = _spec(
        _cut(1, scene_failure={"kind": "safety"}),
        _cut(2, scene_failure={"kind": "safety"}),
        _cut(3, scene_ref=_REF),
    )
    with pytest.raises(VideoSpecError, match="장면"):
        render_scene_video(spec, synthesize=tone_wav, fetch_image=lambda ref: _scene_png())


def test_refusal_happens_before_paying_for_tts() -> None:
    """TTS는 유료다 — 어차피 거부할 영상에 돈을 쓰지 않는다."""
    calls = 0

    def counting(text: str, *, voice: str) -> bytes:
        nonlocal calls
        calls += 1
        return tone_wav(text, voice=voice)

    spec = _spec(_cut(1, scene_failure={"kind": "safety"}), _cut(2, scene_ref=_REF))
    with pytest.raises(VideoSpecError):
        render_scene_video(spec, synthesize=counting, fetch_image=lambda ref: _scene_png())
    assert calls == 0


def test_media_binding_stores_mp4() -> None:
    from sns.render.storage import InMemoryMediaStore
    from sns.render.video.gen.media import SceneRenderMedia

    store = InMemoryMediaStore()
    ref = store.put(_scene_png(), checksum="a" * 64, kind="image", ext="png")
    media = SceneRenderMedia(store, synthesize=tone_wav, topic_major="요리")
    spec_dict = {
        "topic": "자취 요리",
        "method": "generated_scene",
        "slides": [_cut(1, scene_ref=ref), _cut(2, scene_ref=ref)],
    }
    asset = media(spec_dict, "video")
    assert asset.kind == "video"
    assert hashlib.sha256(store.blobs[asset.storage_url]).hexdigest() == asset.checksum
