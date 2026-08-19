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
from sns.render.video.media import VideoRenderMedia
from sns.render.video.quality import check_video
from sns.render.video.renderer import MAX_SEGMENT_S, render_video
from sns.render.video.spec import (
    MAX_NARRATION_WIDTH,
    MAX_SLIDES,
    VideoSpecError,
    parse_video_spec,
)
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
    assert render.duration_s == pytest.approx(sum(render.cut_durations_s))


def test_render_deterministic() -> None:
    a = render_video(SPEC, synthesize=tone_wav)
    b = render_video(SPEC, synthesize=tone_wav)
    assert hashlib.sha256(a.mp4).digest() == hashlib.sha256(b.mp4).digest()


def test_duration_limit_enforced() -> None:
    # 컷별 상한(문장 폭)은 지키되 컷 **수**로 총 길이를 넘긴다 — 총 길이 방어선은 렌더러 몫.
    sentence = "a" * (MAX_NARRATION_WIDTH - 1) + "."
    long_spec = parse_video_spec({"slides": [sentence] * MAX_SLIDES})
    with pytest.raises(VideoSpecError, match="총 길이"):
        render_video(long_spec, synthesize=tone_wav)


def test_one_segment_per_sentence() -> None:
    """나레이션 문장마다 컷이 생겨 화면이 문장 단위로 바뀐다 (FR-A2)."""
    spec = parse_video_spec(
        {"slides": [{"title": "제목", "narration": "첫 문장입니다. 둘째 문장입니다."}]}
    )
    render = render_video(spec, synthesize=tone_wav)
    assert len(render.cut_durations_s) == 2


def test_cut_durations_sum_to_total() -> None:
    spec = parse_video_spec(
        {"slides": [{"title": "가", "narration": "한 문장. 두 문장."}, {"title": "나"}]}
    )
    render = render_video(spec, synthesize=tone_wav)
    assert len(render.cut_durations_s) == 3
    assert render.duration_s == pytest.approx(sum(render.cut_durations_s))


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


# ── 화면 세그먼트 길이 강제 (FR-A2) ─────────────────────────────────
# TTS 발화 길이는 텍스트로 예측·통제가 불가능하다(같은 문장이 호출마다 다르고,
# 숫자·기호는 폭 대비 2배 넘게 걸린다). 그래서 화면 전환 주기는 스펙 상한이 아니라
# **실측 WAV 길이 기준으로 렌더러가** 보장한다.


def test_long_cut_split_into_multiple_visual_segments() -> None:
    sentence = "a" * (MAX_NARRATION_WIDTH - 1) + "."  # tone_wav → 4.1초 (>4.0)
    spec = parse_video_spec({"slides": [{"title": "제목", "narration": sentence}]})
    render = render_video(spec, synthesize=tone_wav)
    assert len(render.cut_durations_s) == 1  # 오디오는 한 컷 그대로
    assert len(render.segment_durations_s) == 2  # 화면만 둘로 쪼갬


def test_every_segment_within_max_duration() -> None:
    sentence = "a" * (MAX_NARRATION_WIDTH - 1) + "."
    spec = parse_video_spec({"slides": [{"title": "가", "narration": sentence}, {"title": "나"}]})
    render = render_video(spec, synthesize=tone_wav)
    assert all(d <= MAX_SEGMENT_S + 1e-6 for d in render.segment_durations_s)


def test_segments_sum_to_total_duration() -> None:
    spec = parse_video_spec({"slides": [{"title": "가", "narration": "짧은 문장. 또 하나."}]})
    render = render_video(spec, synthesize=tone_wav)
    assert render.duration_s == pytest.approx(sum(render.segment_durations_s))


def test_short_cut_stays_single_segment() -> None:
    spec = parse_video_spec({"slides": [{"title": "짧다"}]})
    render = render_video(spec, synthesize=tone_wav)
    assert len(render.segment_durations_s) == 1
