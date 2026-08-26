"""`RenderMedia` 계약의 생성 장면 구현 — [sns.render.video.media]와 같은 바인딩 패턴.

checksum은 산출 mp4 바이트의 sha256이다. 장면은 이미 저장소에 못박혀 있으므로
(`scene_ref`) 같은 spec은 같은 프레임을 만든다 — 결정론이 여기서 끊기지 않는다.
"""

import hashlib
from collections.abc import Mapping

from sns.render.storage import InMemoryMediaStore, MediaStore
from sns.render.video.assemble import VideoRender
from sns.render.video.gen.renderer import render_scene_video
from sns.render.video.spec import parse_video_spec
from sns.render.video.tts import Synthesize, synthesize_google
from sns.tools.contracts import MediaAsset, MediaKind, RenderMedia
from sns.topic_policy import DEV_MAJOR


class SceneRenderMedia:
    """생성 장면 렌더러를 `RenderMedia` 계약에 바인딩. kind는 'video'만."""

    def __init__(
        self,
        store: MediaStore,
        *,
        synthesize: Synthesize,
        topic_major: str,
        font_path: str | None = None,
        ffmpeg: str = "ffmpeg",
    ) -> None:
        self._store = store
        self._synthesize = synthesize
        self._topic_major = topic_major
        self._font_path = font_path
        self._ffmpeg = ffmpeg

    def render(self, media_spec: Mapping[str, object]) -> VideoRender:
        """렌더 결과를 그대로 반환 — 품질 검사가 mp4 바이트를 참조한다."""
        return render_scene_video(
            parse_video_spec(media_spec, topic_major=self._topic_major),
            synthesize=self._synthesize,
            fetch_image=self._store.get,
            font_path=self._font_path,
            ffmpeg=self._ffmpeg,
        )

    def __call__(self, media_spec: Mapping[str, object], kind: MediaKind) -> MediaAsset:
        if kind != "video":
            raise ValueError(f"생성 장면 렌더러가 처리할 수 없는 kind: {kind}")
        render = self.render(media_spec)
        checksum = hashlib.sha256(render.mp4).hexdigest()
        storage_url = self._store.put(render.mp4, checksum=checksum, kind=kind, ext="mp4")
        return MediaAsset(kind=kind, storage_url=storage_url, checksum=checksum)


# 계약 적합성을 mypy가 강제 (video/media.py와 같은 패턴).
_check_scene_render: RenderMedia = SceneRenderMedia(
    InMemoryMediaStore(), synthesize=synthesize_google, topic_major=DEV_MAJOR
)
