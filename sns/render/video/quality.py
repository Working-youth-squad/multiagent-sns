"""영상 발행 전 품질 검사 (FR-Q1·A2의 영상 몫 — C3 gate.py가 C4 소관으로 남긴 것).

ffprobe/ffmpeg로 실제 산출 mp4를 검사한다: 세로 9:16·최소 해상도, 길이 범위,
오디오 존재·무음 아님. 코드 상수 임계값(C3 gate와 같은 방식) — 튜닝은 코드 리뷰로.
"""

import json
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from sns.quality.gate import QualityCheck, QualityReport
from sns.tools.contracts import ContentFormat, MediaAsset

MIN_WIDTH = 1080
MIN_HEIGHT = 1920
MAX_DURATION_S = 180.0
# 이 아래면 사실상 무음으로 판정 (FR-A2 음소거 검사).
MIN_MEAN_VOLUME_DB = -60.0


@dataclass(frozen=True)
class VideoQualityReport:
    passed: bool
    failures: tuple[str, ...]


def _probe(path: Path, ffprobe: str) -> dict[str, object]:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe 실패: {result.stderr.strip()}")
    data: dict[str, object] = json.loads(result.stdout)
    return data


def _mean_volume_db(path: Path, ffmpeg: str) -> float | None:
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    match = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", result.stderr)
    return float(match.group(1)) if match else None


def check_video(
    mp4: bytes, *, ffprobe: str = "ffprobe", ffmpeg: str = "ffmpeg"
) -> VideoQualityReport:
    """산출 mp4의 규격 검사. 실패 항목을 사람이 읽을 문장으로 수집."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "check.mp4"
        path.write_bytes(mp4)
        probe = _probe(path, ffprobe)

        streams = probe.get("streams")
        assert isinstance(streams, list)
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if video is None:
            failures.append("비디오 스트림 없음")
        else:
            width, height = int(video["width"]), int(video["height"])
            if width < MIN_WIDTH or height < MIN_HEIGHT:
                failures.append(f"해상도 {width}×{height} < 최소 {MIN_WIDTH}×{MIN_HEIGHT}")
            if width * 16 != height * 9:
                failures.append(f"비율 {width}×{height} — 세로 9:16 아님")

        fmt = probe.get("format")
        assert isinstance(fmt, dict)
        duration = float(str(fmt.get("duration", "0")))
        if not 0.0 < duration <= MAX_DURATION_S:
            failures.append(f"길이 {duration:.1f}s — (0, {MAX_DURATION_S:.0f}]s 범위 밖")

        if audio is None:
            failures.append("오디오 스트림 없음")
        else:
            mean_db = _mean_volume_db(path, ffmpeg)
            if mean_db is None:
                failures.append("볼륨 측정 실패")
            elif mean_db <= MIN_MEAN_VOLUME_DB:
                failures.append(
                    f"평균 볼륨 {mean_db:.1f}dB ≤ {MIN_MEAN_VOLUME_DB:.0f}dB — 무음 판정"
                )

    return VideoQualityReport(passed=not failures, failures=tuple(failures))


# 사이클의 `AssessQuality`와 구조적으로 같다. Protocol을 import하면 순환이 난다 —
# sns.runner.cycle → sns.render.video.spec → sns.render.video(패키지 init) → 이 모듈.
AssessQualityFn = Callable[..., QualityReport]
# 저장된 산출물을 되읽는 seam — `MediaStore.get`이 그대로 들어온다.
FetchMedia = Callable[[str], bytes]


def make_video_gate(
    fetch: FetchMedia, *, ffprobe: str = "ffprobe", ffmpeg: str = "ffmpeg"
) -> AssessQualityFn:
    """영상 품질 게이트를 사이클의 `assess_quality` 형태로 조립.

    **전달받은 `media`를 읽는다 — 다시 렌더하지 않는다.** 영상은 컷마다 유료 TTS를
    사므로 재렌더는 비용이 두 배다. 러너가 렌더 결과(`MediaAsset`)를 그대로 넘겨주므로
    ([sns.runner.cycle]) 저장소에서 읽으면 된다.

    **되읽기는 `fetch`(= `MediaStore.get`)로 한다.** `storage_url`을 파일 경로로
    간주하면 안 된다 — 저장소 벤더 교체 seam(FR-M3)이라 `file://` URI나 `s3://`가
    올 수 있고, 실제로 로컬 스크립트 둘이 서로 다른 형식을 내보내 게이트가 터졌다.
    """

    def assess(
        *, media_spec: Mapping[str, object], media: MediaAsset, content_format: ContentFormat
    ) -> QualityReport:
        report = check_video(fetch(media.storage_url), ffprobe=ffprobe, ffmpeg=ffmpeg)
        checks = tuple(QualityCheck(f, False, f) for f in report.failures) or (
            QualityCheck("video_spec", True, "규격·길이·오디오 하한 통과"),
        )
        return QualityReport(status="passed" if report.passed else "failed", checks=checks)

    return assess
