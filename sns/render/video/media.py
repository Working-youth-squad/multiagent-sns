"""`RenderMedia` 계약의 영상 구현 — parse → TTS·합성 → 저장 → `MediaAsset`.

C3 카드 media.py와 같은 바인딩 패턴. checksum은 산출 mp4 바이트의 sha256.
TTS 바이트는 재호출 시 달라질 수 있으므로 spec 수준 결정론(같은 spec → 같은
checksum)은 TTS 캐시 도입 후의 후속 — 지금 보장은 "같은 바이트 → 같은 checksum".
"""

import hashlib
from collections.abc import Mapping

from sns.render.storage import InMemoryMediaStore, MediaStore
from sns.render.video.renderer import VideoRender, render_video
from sns.render.video.spec import parse_video_spec
from sns.render.video.tts import Synthesize, synthesize_google
from sns.tools.contracts import MediaAsset, MediaKind, RenderMedia


class VideoRenderMedia:
    """영상 렌더러를 `RenderMedia` 계약에 바인딩. kind는 'video'만."""

    def __init__(
        self,
        store: MediaStore,
        *,
        synthesize: Synthesize,
        font_path: str | None = None,
        ffmpeg: str = "ffmpeg",
    ) -> None:
        self._store = store
        self._synthesize = synthesize
        self._font_path = font_path
        self._ffmpeg = ffmpeg

    def render(self, media_spec: Mapping[str, object]) -> VideoRender:
        """렌더 결과를 그대로 반환 — 품질 검사가 mp4 바이트를 참조한다."""
        return render_video(
            parse_video_spec(media_spec),
            synthesize=self._synthesize,
            font_path=self._font_path,
            ffmpeg=self._ffmpeg,
        )

    def __call__(self, media_spec: Mapping[str, object], kind: MediaKind) -> MediaAsset:
        if kind != "video":
            raise ValueError(f"영상 렌더러가 처리할 수 없는 kind: {kind}")
        render = self.render(media_spec)
        checksum = hashlib.sha256(render.mp4).hexdigest()
        storage_url = self._store.put(render.mp4, checksum=checksum, kind=kind, ext="mp4")
        return MediaAsset(kind=kind, storage_url=storage_url, checksum=checksum)


# 계약 적합성을 mypy가 강제 (fakes.py의 _check_* 패턴과 동일).
_check_video_render: RenderMedia = VideoRenderMedia(
    InMemoryMediaStore(), synthesize=synthesize_google
)
