"""TTS seam — 슬라이드 텍스트 → WAV(LINEAR16) 바이트.

Chirp 3 HD는 SSML 미지원 리스크가 있어 `<mark>` 타임스탬프를 쓰지 않는다. 대신
슬라이드 1장당 1회 합성하고, 각 WAV 길이가 곧 슬라이드/자막 타이밍이 된다(06 §3).
렌더러·테스트는 `Synthesize` 프로토콜로 주입받으므로 네트워크 없는 가짜로 대체 가능.
"""

import io
import os
import wave
from typing import Protocol

ENV_TTS_API_KEY = "GOOGLE_TTS_API_KEY"
SAMPLE_RATE_HZ = 24000


class TtsError(RuntimeError):
    """TTS 합성 실패 — 설정 누락 포함."""


class Synthesize(Protocol):
    """텍스트 1건 → WAV(LINEAR16) 바이트."""

    def __call__(self, text: str, *, voice: str) -> bytes: ...


def synthesize_google(text: str, *, voice: str) -> bytes:
    """Google Cloud TTS 합성. API 키는 env `GOOGLE_TTS_API_KEY`."""
    api_key = os.environ.get(ENV_TTS_API_KEY)
    if not api_key:
        raise TtsError(f"env {ENV_TTS_API_KEY} 누락 — GCP 콘솔에서 TTS 제한 API 키 발급 필요")
    # 지연 임포트: 오프라인 테스트가 google.cloud 임포트 비용/의존을 지지 않게.
    from google.cloud import texttospeech

    client = texttospeech.TextToSpeechClient(client_options={"api_key": api_key})
    language_code = "-".join(voice.split("-")[:2])  # "ko-KR-Chirp3-HD-Charon" → "ko-KR"
    response = client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(language_code=language_code, name=voice),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=SAMPLE_RATE_HZ,
        ),
    )
    return bytes(response.audio_content)


def wav_duration_s(wav: bytes) -> float:
    """WAV 길이(초) — stdlib `wave`만 사용, ffprobe 불필요."""
    with wave.open(io.BytesIO(wav)) as f:
        return float(f.getnframes()) / float(f.getframerate())


def silent_wav(duration_s: float) -> bytes:
    """고정 무음 WAV — 오프라인 테스트용 가짜 `Synthesize`의 재료."""
    frames = int(duration_s * SAMPLE_RATE_HZ)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE_HZ)
        f.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()
