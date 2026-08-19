"""영상 렌더 통합 — 가짜 TTS(사인파)로 ffmpeg 합성, 품질 검사, 결정론.

네트워크 0. ffmpeg 없는 환경(로컬 최소 셋업)은 skip — CI는 ffmpeg 설치 후 실검증.
"""

import hashlib
import io
import math
import shutil
import wave

import pytest

from sns.render.storage import InMemoryMediaStore
from sns.render.video import renderer as renderer_mod
from sns.render.video.media import VideoRenderMedia
from sns.render.video.quality import check_video
from sns.render.video.renderer import FontNotFoundError, render_video
from sns.render.video.spec import VideoSpecError, parse_video_spec
from sns.render.video.tts import SAMPLE_RATE_HZ, wav_duration_s

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe 필요 — CI에서 설치·실행",
)


def tone_wav(text: str, *, voice: str) -> bytes:
    """가짜 Synthesize — 텍스트 길이에 비례하는 440Hz 사인파(무음 아님, 결정론)."""
    duration_s = 1.0 + 0.05 * len(text)
    frames = int(duration_s * SAMPLE_RATE_HZ)
    pcm = b"".join(
        int(12000 * math.sin(2 * math.pi * 440 * i / SAMPLE_RATE_HZ)).to_bytes(
            2, "little", signed=True
        )
        for i in range(frames)
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE_HZ)
        f.writeframes(pcm)
    return buf.getvalue()


SPEC = parse_video_spec({"slides": ["멀티에이전트 SNS", "영상도 코드로 만든다"]})


def test_render_passes_quality_gate() -> None:
    render = render_video(SPEC, synthesize=tone_wav)
    report = check_video(render.mp4)
    assert report.passed, report.failures
    assert render.duration_s == pytest.approx(sum(render.slide_durations_s))


def test_render_deterministic() -> None:
    a = render_video(SPEC, synthesize=tone_wav)
    b = render_video(SPEC, synthesize=tone_wav)
    assert hashlib.sha256(a.mp4).digest() == hashlib.sha256(b.mp4).digest()


def test_duration_limit_enforced() -> None:
    long_spec = parse_video_spec({"slides": ["아주 긴 나레이션 " * 30] * 20})
    with pytest.raises(VideoSpecError):
        render_video(long_spec, synthesize=tone_wav)


def test_media_binding_stores_mp4() -> None:
    store = InMemoryMediaStore()
    render_media = VideoRenderMedia(store, synthesize=tone_wav)
    asset = render_media({"slides": ["바인딩 테스트"]}, "video")
    assert asset.kind == "video"
    assert asset.storage_url.endswith(".mp4")
    assert hashlib.sha256(store.blobs[asset.storage_url]).hexdigest() == asset.checksum
    with pytest.raises(ValueError):
        render_media({"slides": ["x"]}, "image")


def test_fake_tts_duration_helper() -> None:
    wav = tone_wav("열두글자짜리텍스트입니다", voice="ignored")
    assert wav_duration_s(wav) == pytest.approx(1.0 + 0.05 * 12, abs=0.01)


def test_missing_cjk_font_raises_instead_of_tofu(monkeypatch: pytest.MonkeyPatch) -> None:
    """CJK 폰트가 하나도 없으면 내장 폰트로 조용히 폴백(=한글 두부)하지 않고 실패한다.

    fonts-noto-cjk 미설치 컨테이너에서 깨진 자막 영상이 발행 파이프라인을 통과하던 회귀 방지.
    """
    monkeypatch.setattr(renderer_mod, "_FONT_CANDIDATES", ())
    with pytest.raises(FontNotFoundError):
        render_video(SPEC, synthesize=tone_wav)
