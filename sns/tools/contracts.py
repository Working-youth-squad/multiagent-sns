"""툴 계약 6종 (FR-C2) — 서브에이전트가 외부와 상호작용하는 유일한 seam.

동결: 이 시그니처의 변경은 반드시 PR + 상대 리뷰 (docs/plan/14-태스크분할.md §0-1).
deepagents 결합(T0-4)은 이 계약 위의 래퍼로 — 계약 자체는 프레임워크 무관 순수 Python.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

Platform = Literal["instagram", "youtube"]
ContentFormat = Literal["feed_image", "reels", "shorts"]
MediaKind = Literal["image", "video", "thumbnail", "audio"]
PlaybookScope = Literal["global", "platform", "format", "topic"]

# 오류 분류 (FR-P4) — 미분류는 error_raw 원문 보존
ErrorClass = Literal["auth", "quota", "spam_block", "transient", "permanent_unknown"]


@dataclass(frozen=True)
class ToolError:
    error_class: ErrorClass
    error_raw: str


# ── research_trends (FR-G4) ─────────────────────────────────────────


@dataclass(frozen=True)
class SourceResult:
    source: str
    ok: bool
    items: tuple[str, ...] = ()  # 실패(ok=False) 시 빈 튜플 — 예외 없이 격리


@dataclass(frozen=True)
class TrendDigest:
    digest_markdown: str
    source_results: tuple[SourceResult, ...]


class ResearchTrends(Protocol):
    def __call__(self, sources: tuple[str, ...] | None = None, limit: int = 10) -> TrendDigest: ...


# ── render_media (FR-M1~M3) ─────────────────────────────────────────


@dataclass(frozen=True)
class MediaAsset:
    kind: MediaKind
    storage_url: str  # 저장소 벤더 교체 seam (FR-M3)
    checksum: str  # 같은 spec → 같은 checksum (FR-M1)


class RenderMedia(Protocol):
    def __call__(self, media_spec: Mapping[str, object], kind: MediaKind) -> MediaAsset: ...


# ── publish (FR-P1~P4) ──────────────────────────────────────────────


@dataclass(frozen=True)
class PublishResult:
    post_id: str | None = None
    container_id: str | None = None  # IG 2단계 중간 상태 보존 (재시작 시 재사용)
    error: ToolError | None = None


class Publish(Protocol):
    def __call__(
        self,
        platform: Platform,
        media: MediaAsset,
        caption: str,
        idempotency_key: str,
        container_id: str | None = None,
    ) -> PublishResult: ...


# ── poll_metrics (FR-L1 — metric_key 표준 = 11-데이터모델 §4) ───────


@dataclass(frozen=True)
class MetricValue:
    metric_key: str
    value: float | None
    missing: bool  # 결측=NULL (NFR-3): DB의 metric_missing_xor CHECK와 동일 불변식

    def __post_init__(self) -> None:
        if self.missing != (self.value is None):
            raise ValueError(f"missing XOR value 위반: {self!r}")


class PollMetrics(Protocol):
    def __call__(
        self, platform: Platform, post_id: str, window_index: int
    ) -> tuple[MetricValue, ...]: ...


# ── read_stats (FR-L3) ──────────────────────────────────────────────


@dataclass(frozen=True)
class TopicStat:
    topic_id: str
    format: ContentFormat
    platform: Platform
    trials: int
    reward_sum: float


class ReadStats(Protocol):
    def __call__(self, platform: Platform | None = None) -> tuple[TopicStat, ...]: ...


# ── write_playbook (FR-L4 — LLM 착지점, FR-C4) ──────────────────────


@dataclass(frozen=True)
class PlaybookVersion:
    scope: PlaybookScope
    scope_ref: str | None
    version: int


class WritePlaybook(Protocol):
    def __call__(
        self, scope: PlaybookScope, guidance: str, scope_ref: str | None = None
    ) -> PlaybookVersion: ...
