"""`RenderMedia` 계약의 영상 구현 — parse → TTS·합성 → 저장 → `MediaAsset`.

C3 카드 media.py와 같은 바인딩 패턴. checksum은 산출 mp4 바이트의 sha256.
TTS 바이트는 재호출 시 달라질 수 있으므로 spec 수준 결정론(같은 spec → 같은
checksum)은 TTS 캐시 도입 후의 후속 — 지금 보장은 "같은 바이트 → 같은 checksum".
"""

import hashlib
from collections.abc import Mapping

from sns.render.storage import InMemoryMediaStore, MediaStore
from sns.render.video.motion import render_motion_video
from sns.render.video.renderer import VideoRender, render_video
from sns.render.video.spec import parse_video_spec
from sns.render.video.tts import Synthesize, synthesize_google
from sns.tools.contracts import MediaAsset, MediaKind, RenderMedia

# 화면 문법 레지스트리 — 키는 [sns.render.video.spec.VIDEO_STYLES]와 함께 늘린다.
# 완전 생성 클립(generated_clip) 트랙은 여기에 "clip" 렌더러로 추가될 예정(팀원 작업).
_RENDERERS = {
    "": render_video,  # 3단 레이아웃
    "motion": render_motion_video,  # 모션 그래픽
}


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
        """렌더 결과를 그대로 반환 — 품질 검사가 mp4 바이트를 참조한다.

        spec의 `style`이 템플릿을 고른다 — 승인 웹 재렌더가 별도 배선 없이
        같은 스타일로 다시 그려지는 근거. 새 화면 문법(예: 완전 생성 클립)은
        `_RENDERERS`에 항목 하나로 추가한다 — 캐릭터 트랙([sns.onboarding.character],
        spec의 character_ref)과는 독립이다: 캐릭터는 재료, style은 화면 문법.
        """
        spec = parse_video_spec(media_spec)
        render_fn = _RENDERERS[spec.style]
        return render_fn(
            spec,
            synthesize=self._synthesize,
            font_path=self._font_path,
            fetch_image=self._store.get,
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
