"""렌더 산출물 저장소 seam (FR-M3) — 벤더 교체 1지점.

카드·영상 렌더러가 공유한다. 운영 구현(GCS 등)은 후속. 여기선 계약과
콘텐츠 주소화(content-addressed) 인메모리 구현만 둔다 — checksum이 곧 키라
같은 자산 재저장은 같은 URL을 낳는다(멱등, FR-M1과 정합).
"""

from typing import Protocol

from sns.tools.contracts import MediaKind


class MediaStore(Protocol):
    """렌더 바이트 → 안정 URL. 반환 URL은 MediaAsset.storage_url에 실린다."""

    def put(self, data: bytes, *, checksum: str, kind: MediaKind, ext: str) -> str: ...


class InMemoryMediaStore:
    """콘텐츠 주소화 인메모리 저장소 — 테스트·개발용.

    URL = ``mem://{kind}/{checksum}.{ext}``. 같은 checksum 재저장은 같은 URL.
    """

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def put(self, data: bytes, *, checksum: str, kind: MediaKind, ext: str) -> str:
        url = f"mem://{kind}/{checksum}.{ext}"
        self.blobs[url] = data
        return url
