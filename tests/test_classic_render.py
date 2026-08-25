"""영상 렌더 통합 — 가짜 TTS(사인파)로 ffmpeg 합성, 품질 검사, 결정론.

네트워크 0. ffmpeg 없는 환경(로컬 최소 셋업)은 skip — CI는 ffmpeg 설치 후 실검증.
"""

import hashlib
import io
import math
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import pytest
from PIL import Image

from sns.render.fonts import FontNotFoundError
from sns.render.video.classic import renderer as renderer_mod
from sns.render.video.classic.renderer import (
    _ACCENT_Y_RATIO,
    _BAR_RATIO,
    MAX_SEGMENT_S,
    render_video,
)
from sns.render.video.classic.spec import (
    MAX_NARRATION_WIDTH,
    MAX_SLIDES,
    VideoSpecError,
    parse_video_spec,
)
from sns.render.video.quality import check_video
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


def _stream_duration(mp4: bytes, kind: str) -> float:
    """산출 mp4에서 실제 스트림 길이(초). 계산값이 아니라 **파일**을 본다."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.mp4"
        path.write_bytes(mp4)
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", kind,
             "-show_entries", "stream=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        )  # fmt: skip
    return float(out.stdout.strip())


def test_rendered_video_stream_matches_audio() -> None:
    """영상 트랙이 오디오보다 짧으면 뒷부분이 정지 화면으로 재생된다.

    concat 목록이 세그먼트 수와 어긋나면 조용히 잘리는데, 계산값끼리 비교하는
    단언으로는 못 잡는다 — 실제 파일의 스트림 길이를 본다.
    """
    sentence = "a" * (MAX_NARRATION_WIDTH - 1) + "."  # 4.1초 → 세그먼트 2개로 분할
    spec = parse_video_spec({"slides": [{"title": "가", "narration": sentence}, {"title": "나"}]})
    render = render_video(spec, synthesize=tone_wav)
    video = _stream_duration(render.mp4, "v:0")
    audio = _stream_duration(render.mp4, "a:0")
    assert video == pytest.approx(audio, abs=0.15), f"영상 {video:.2f}s vs 오디오 {audio:.2f}s"
    assert video == pytest.approx(render.duration_s, abs=0.15)


def _accent_fill_ratio(mp4: bytes, at_s: float, *, spec_height: int = 1920) -> float:
    """재생 at_s 시점 프레임에서 하단 진행바가 채워진 가로 비율."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "v.mp4").write_bytes(mp4)
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at_s:.2f}",
             "-i", str(d / "v.mp4"), "-frames:v", "1", str(d / "f.png")],
            check=True,
        )  # fmt: skip
        img = Image.open(d / "f.png").convert("RGB")
        width, height = img.size
        row = height - max(round(spec_height * _BAR_RATIO), 4) // 2 - 1
        filled = sum(
            1
            for x in range(width)
            if (px := img.getpixel((x, row)))[2] > 120 and px[2] > px[0] + 40
        )
    return filled / width


def test_progress_bar_advances_over_time() -> None:
    """하단 진행바는 재생이 진행될수록 차야 한다.

    drawbox의 `t`는 타임스탬프가 아니라 선 두께라, w 표현식에 쓰면 매 프레임 같은
    값이 나와 바가 처음부터 꽉 찬 채로 멈춘다(실제로 그랬다).
    """
    spec = parse_video_spec(
        {"slides": [{"title": f"{i}장", "narration": "한 문장."} for i in range(4)]}
    )
    render = render_video(spec, synthesize=tone_wav)
    early = _accent_fill_ratio(render.mp4, render.duration_s * 0.2)
    late = _accent_fill_ratio(render.mp4, render.duration_s * 0.8)
    assert early < 0.4, f"초반에 이미 {early:.0%} 차 있음"
    assert late > 0.6, f"후반에도 {late:.0%}뿐"
    assert early < late


def _accent_bar_width(mp4: bytes, at_s: float, *, row_ratio: float) -> int:
    """지정 시각·지정 높이 비율에서 액센트색 픽셀의 가로 길이(px)."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "v.mp4").write_bytes(mp4)
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at_s:.2f}",
             "-i", str(d / "v.mp4"), "-frames:v", "1", str(d / "f.png")],
            check=True,
        )  # fmt: skip
        img = Image.open(d / "f.png").convert("RGB")
        width, height = img.size
        row = round(height * row_ratio)
        return sum(
            1
            for x in range(width)
            if (px := img.getpixel((x, row)))[2] > 120 and px[2] > px[0] + 40
        )


def test_accent_bar_width_is_constant() -> None:
    """액센트 바는 크기가 변하지 않는다.

    컷 전환마다 짧게 줄었다 늘어나게 해봤지만 130px짜리 요소의 미세한 변화라
    전환 신호로 읽히지 않고 잔떨림으로만 보였다 — 고정 기준선으로 되돌린다.
    """
    spec = parse_video_spec(
        {"slides": [{"title": f"{i}장", "narration": "충분히 긴 한 문장입니다."} for i in range(3)]}
    )
    render = render_video(spec, synthesize=tone_wav)
    starts = [0.0]
    for d in render.cut_durations_s[:-1]:
        starts.append(starts[-1] + d)
    widths = [
        _accent_bar_width(render.mp4, at, row_ratio=_ACCENT_Y_RATIO)
        for at in (starts[1] + 0.05, starts[1] + render.cut_durations_s[1] * 0.6, starts[2] + 0.05)
    ]
    assert widths[0] > 0, "액센트 바가 보이지 않음"
    assert len(set(widths)) == 1, f"폭이 흔들림: {widths}"


def test_accent_bar_position_is_stable_across_title_lengths() -> None:
    """제목 줄 수가 달라도 액센트 바는 같은 높이에 있다 — 시선 기준점."""
    one_line = parse_video_spec({"slides": [{"title": "짧다", "narration": "한 문장."}]})
    two_line = parse_video_spec(
        {"slides": [{"title": "제법 긴 제목이라 두 줄로 갈린다", "narration": "한 문장."}]}
    )
    a = render_video(one_line, synthesize=tone_wav)
    b = render_video(two_line, synthesize=tone_wav)
    wa = _accent_bar_width(a.mp4, a.duration_s * 0.6, row_ratio=_ACCENT_Y_RATIO)
    wb = _accent_bar_width(b.mp4, b.duration_s * 0.6, row_ratio=_ACCENT_Y_RATIO)
    assert wa > 0 and wb > 0, f"바가 그 높이에 없음: {wa}px / {wb}px"


def test_missing_cjk_font_raises_instead_of_tofu(monkeypatch: pytest.MonkeyPatch) -> None:
    """CJK 폰트가 하나도 없으면 내장 폰트로 조용히 폴백(=한글 두부)하지 않고 실패한다.

    fonts-noto-cjk 미설치 컨테이너에서 깨진 자막 영상이 발행 파이프라인을 통과하던 회귀 방지.
    """
    monkeypatch.setattr(renderer_mod, "_FONT_CANDIDATES", ())
    with pytest.raises(FontNotFoundError):
        render_video(SPEC, synthesize=tone_wav)
