"""영상 렌더 검증 — 3단 레이아웃·결정론(FR-M1)·저장 seam(FR-M3)·계약 바인딩.

네트워크 0: TTS는 텍스트 길이에 비례하는 사인파 가짜로 대체한다.
ffmpeg 없는 환경(로컬 최소 셋업)은 skip — CI는 설치 후 실검증.

**계산값끼리 비교하는 단언을 쓰지 않는다.** 예전 테스트가
`duration_s == sum(cut_durations_s)`처럼 둘 다 WAV에서 계산한 값을 비교해,
concat 목록이 어긋나 영상이 잘려도 통과했다. 산출 mp4를 ffprobe로 직접 재고,
프레임을 뽑아 픽셀을 센다.
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
from sns.render.storage import InMemoryMediaStore
from sns.render.video import renderer as renderer_mod
from sns.render.video.media import VideoRenderMedia
from sns.render.video.quality import check_video
from sns.render.video.renderer import _BAR_RATIO, render_video
from sns.render.video.spec import MAX_SLIDES, VideoSpecError, parse_video_spec

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe 필요 — CI에서 설치·실행",
)

SPEC_DICT: dict[str, object] = {
    "topic": "리스트에서 in 쓰지 마세요",
    "slides": [
        {
            "subtitle": "왜 느린가",
            "narration": "in 연산자는 처음부터 끝까지 훑습니다.",
            "code": "items = load_ids()\n\nif target in items:\n    handle(target)",
            "lang": "python",
            "focus_lines": [3],
        },
        {"subtitle": "해법", "narration": "셋으로 바꾸면 한 번입니다."},
    ],
}
SPEC = parse_video_spec(SPEC_DICT)
SAMPLE_RATE_HZ = 24000


# 초당 발화 속도. 0.05는 실제 TTS(한국어 8자/초 ≈ 0.125)보다 빨라 스위트를 가볍게
# 유지하려고 고른 값이다. **길이 자체가 단언 대상인 테스트**는 실측 속도를 써야 한다 —
# 빠른 값으로는 60컷을 붙여도 180초 상한에 닿지 않아 상한 테스트가 헛돈다(실제로 그랬다).
FAST_S_PER_CHAR = 0.05
CHIRP_S_PER_CHAR = 0.125


def _tone_wav(text: str, *, s_per_char: float) -> bytes:
    """가짜 Synthesize — 텍스트 길이에 비례하는 440Hz 사인파(무음 아님, 결정론)."""
    duration_s = 1.0 + s_per_char * len(text)
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


def tone_wav(text: str, *, voice: str) -> bytes:
    return _tone_wav(text, s_per_char=FAST_S_PER_CHAR)


def chirp_pace_wav(text: str, *, voice: str) -> bytes:
    return _tone_wav(text, s_per_char=CHIRP_S_PER_CHAR)


def _probe(mp4: bytes, stream: str, entries: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "p.mp4"
        path.write_bytes(mp4)
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", stream,
             "-show_entries", entries, "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        )  # fmt: skip
    return out.stdout.strip()


def _frame_at(mp4: bytes, at_s: float) -> Image.Image:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "v.mp4").write_bytes(mp4)
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at_s:.2f}",
             "-i", str(d / "v.mp4"), "-frames:v", "1", str(d / "f.png")],
            check=True,
        )  # fmt: skip
        return Image.open(d / "f.png").convert("RGB")


def test_render_passes_quality_gate() -> None:
    render = render_video(SPEC, synthesize=tone_wav)
    report = check_video(render.mp4)
    assert report.passed, report.failures
    assert len(render.cut_durations_s) == len(SPEC.slides)


def test_render_deterministic() -> None:
    a = render_video(SPEC, synthesize=tone_wav)
    b = render_video(SPEC, synthesize=tone_wav)
    assert hashlib.sha256(a.mp4).digest() == hashlib.sha256(b.mp4).digest()


def test_video_stream_matches_audio() -> None:
    """영상이 오디오보다 짧으면 뒷부분이 정지 화면으로 재생된다 — 실제 파일을 잰다."""
    render = render_video(SPEC, synthesize=tone_wav)
    video = float(_probe(render.mp4, "v:0", "stream=duration"))
    audio = float(_probe(render.mp4, "a:0", "stream=duration"))
    assert video == pytest.approx(audio, abs=0.15), f"영상 {video:.2f}s vs 오디오 {audio:.2f}s"
    assert video == pytest.approx(render.duration_s, abs=0.15)


def test_one_cut_one_screen() -> None:
    """슬라이드 1장 = 컷 1개 = 화면 1장. 문장 분할도 세그먼트 등분도 없다."""
    spec = parse_video_spec(
        {
            **SPEC_DICT,
            "slides": [
                {"subtitle": f"{i}단계", "narration": f"{i}번째 문장입니다."} for i in range(4)
            ],
        }
    )
    render = render_video(spec, synthesize=tone_wav)
    assert len(render.cut_durations_s) == 4


def test_duration_limit_enforced() -> None:
    """컷 상한(60장)까지 꽉 채우면 쇼츠 길이 상한을 넘는다 — 렌더 전에 끊는지 본다."""
    one = {"subtitle": "부제", "narration": "가" * 30 + "."}
    long_spec = parse_video_spec({**SPEC_DICT, "slides": [one] * MAX_SLIDES})
    with pytest.raises(VideoSpecError, match="총 길이"):
        render_video(long_spec, synthesize=chirp_pace_wav)


def test_progress_bar_advances_over_time() -> None:
    """하단 진행바는 재생이 진행될수록 차야 한다.

    drawbox의 `t`는 타임스탬프가 아니라 선 두께라, w 표현식에 쓰면 바가 처음부터
    꽉 찬 채로 멈춘다(실제로 그랬다). overlay의 `x`는 `t`가 시각이다.
    """
    spec = parse_video_spec(
        {
            **SPEC_DICT,
            "slides": [
                {"subtitle": f"{i}단계", "narration": f"{i}번째 문장입니다."} for i in range(4)
            ],
        }
    )
    render = render_video(spec, synthesize=tone_wav)
    row_from_bottom = max(round(spec.height * _BAR_RATIO), 4) // 2 + 1

    def fill_ratio(at_s: float) -> float:
        img = _frame_at(render.mp4, at_s)
        w, h = img.size
        row = h - row_from_bottom
        filled = sum(
            1 for x in range(w) if (px := img.getpixel((x, row)))[2] > 120 and px[2] > px[0] + 40
        )
        return filled / w

    early, late = fill_ratio(render.duration_s * 0.2), fill_ratio(render.duration_s * 0.8)
    assert early < 0.4, f"초반에 이미 {early:.0%} 차 있음"
    assert late > 0.6, f"후반에도 {late:.0%}뿐"


def test_topic_band_is_black_ground() -> None:
    """3단 레이아웃의 상·하단은 검은 바탕 — 자막·주제 대비를 보장하는 근거."""
    render = render_video(SPEC, synthesize=tone_wav)
    img = _frame_at(render.mp4, render.duration_s * 0.3)
    assert img.getpixel((10, 10)) == (0, 0, 0)  # 좌상단 여백
    assert img.getpixel((10, SPEC.height - 40)) == (0, 0, 0)  # 좌하단 여백


def test_media_binding_stores_mp4() -> None:
    store = InMemoryMediaStore()
    render_media = VideoRenderMedia(store, synthesize=tone_wav)
    asset = render_media(SPEC_DICT, "video")
    assert asset.kind == "video"
    assert asset.storage_url.endswith(".mp4")
    assert hashlib.sha256(store.blobs[asset.storage_url]).hexdigest() == asset.checksum


def test_media_binding_rejects_non_video_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        VideoRenderMedia(InMemoryMediaStore(), synthesize=tone_wav)(SPEC_DICT, "image")


def test_missing_cjk_font_raises_instead_of_tofu(monkeypatch: pytest.MonkeyPatch) -> None:
    """CJK 폰트가 하나도 없으면 내장 폰트로 조용히 폴백(=한글 두부)하지 않고 실패한다."""
    monkeypatch.setattr(renderer_mod, "_FONT_CANDIDATES", ())
    with pytest.raises(FontNotFoundError):
        render_video(SPEC, synthesize=tone_wav)


def _solid_png(rgb: tuple[int, int, int], side: int = 940) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (side, side), rgb).save(buf, format="PNG")
    return buf.getvalue()


def test_image_ref_fills_the_square() -> None:
    """코드가 없는 컷은 해소된 사진으로 가운데를 채운다 — 그라데이션은 마지막 폴백."""
    magenta = (200, 30, 160)
    spec = parse_video_spec(
        {
            **SPEC_DICT,
            "slides": [
                {
                    "subtitle": "부제",
                    "narration": "사진이 들어가는 컷입니다.",
                    "image_ref": "mem://image/deadbeef.png",
                }
            ],
        }
    )
    render = render_video(spec, synthesize=tone_wav, fetch_image=lambda ref: _solid_png(magenta))
    img = _frame_at(render.mp4, render.duration_s * 0.5)
    center = img.getpixel((spec.width // 2, 360 + 470))
    assert abs(center[0] - magenta[0]) < 25 and abs(center[2] - magenta[2]) < 25, center


def test_image_ref_without_fetch_seam_raises() -> None:
    """조용히 그라데이션으로 떨어지면 배선 실수가 영상까지 흘러간다."""
    spec = parse_video_spec(
        {
            **SPEC_DICT,
            "slides": [
                {"subtitle": "부제", "narration": "한 문장.", "image_ref": "mem://image/x.png"}
            ],
        }
    )
    with pytest.raises(VideoSpecError, match="fetch_image"):
        render_video(spec, synthesize=tone_wav)


def test_concept_fills_the_square() -> None:
    """개념 그림도 정사각을 채운다 — 코드 다음, 사진보다 앞."""
    spec = parse_video_spec(
        {
            **SPEC_DICT,
            "slides": [
                {
                    "subtitle": "부제",
                    "narration": "개념 그림이 들어가는 컷입니다.",
                    "concept": {"kind": "emphasis", "headline": "100억", "tag": "최악의 경우"},
                }
            ],
        }
    )
    render = render_video(spec, synthesize=tone_wav)
    img = _frame_at(render.mp4, render.duration_s * 0.5)
    # 그라데이션이었다면 정사각 가운데가 배경 그라데이션 색이다. 개념 그림은 액센트 글자를 낸다.
    band = [img.getpixel((x, 360 + 470)) for x in range(200, 880, 3)]
    assert any(p[2] > 150 and p[2] > p[0] + 40 for p in band), "액센트 글자가 안 보임"
