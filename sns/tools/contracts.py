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

# 영상 제작 방식. Enum이 아니라 Literal인 이유는 이 값이 media_spec(jsonb)에 그대로
# 저장돼 DB와 LLM 툴 인자를 오가기 때문 — Enum이면 직렬화 층이 하나 는다.
# hybrid는 다른 셋과 층위가 다르다: 별도 제작 방식이 아니라 "컷마다 방식이 다를 수
# 있다"는 선언이라, 검증 단위는 언제나 slide.method다.
VideoMethod = Literal["template", "generated_scene", "generated_clip", "hybrid"]

# 생성 실패의 종류. quota·network는 재시도 가능, safety·provider는 불가 — 같은
# 프롬프트는 같은 안전 판정을 받으므로 다시 부르면 돈만 쓴다. 발행 쪽 `ErrorClass`와
# 이름을 통일하지 않는 이유는 실패의 성격이 달라서다(발행은 계정·플랫폼, 생성은
# 프롬프트·프로바이더). 다만 **재시도 가능/불가라는 축은 같다**.
GenerationFailureKind = Literal["quota", "safety", "network", "provider"]

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
