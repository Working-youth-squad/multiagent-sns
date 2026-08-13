"""`RenderMedia` 계약의 카드 구현 — parse → 렌더 → 저장 → `MediaAsset`.

동결 계약(`sns.tools.contracts.RenderMedia`) 위에서 동작한다. checksum은 렌더된
PNG 바이트의 sha256 — 같은 spec → 같은 바이트 → 같은 checksum(FR-M1). 저장은
주입된 `MediaStore`로만(FR-M3).
"""

import hashlib
from collections.abc import Mapping

from sns.render.card.renderer import CardRender, render_card
from sns.render.card.spec import parse_card_spec
from sns.render.storage import InMemoryMediaStore, MediaStore
from sns.tools.contracts import MediaAsset, MediaKind, RenderMedia

# 카드로 렌더 가능한 kind (영상 kind는 C4가 담당).
_CARD_KINDS: frozenset[MediaKind] = frozenset({"image", "thumbnail"})


class CardRenderMedia:
    """카드 렌더러를 `RenderMedia` 계약에 바인딩. font_path 미지정 시 내장 폰트."""

    def __init__(self, store: MediaStore, *, font_path: str | None = None) -> None:
        self._store = store
        self._font_path = font_path

    def render(self, media_spec: Mapping[str, object]) -> CardRender:
        """렌더 결과를 그대로 반환 — 품질 게이트가 overflow 등을 참조한다."""
        return render_card(parse_card_spec(media_spec), font_path=self._font_path)

    def __call__(self, media_spec: Mapping[str, object], kind: MediaKind) -> MediaAsset:
        if kind not in _CARD_KINDS:
            raise ValueError(f"카드 렌더러가 처리할 수 없는 kind: {kind}")
        render = self.render(media_spec)
        checksum = hashlib.sha256(render.png).hexdigest()
        storage_url = self._store.put(render.png, checksum=checksum, kind=kind, ext="png")
        return MediaAsset(kind=kind, storage_url=storage_url, checksum=checksum)


# 계약 적합성을 mypy가 강제 (fakes.py의 _check_* 패턴과 동일).
_check_card_render: RenderMedia = CardRenderMedia(InMemoryMediaStore())
