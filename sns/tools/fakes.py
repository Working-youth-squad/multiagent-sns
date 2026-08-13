"""툴 계약 6종의 인메모리 결정론 가짜 구현 — 주입식 테스트의 기반 (NFR-2).

같은 입력 → 항상 같은 출력. 상대 트랙(IG/YT)이 없어도 이 가짜로 개발·테스트한다.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping

from sns.tools.contracts import (
    MediaAsset,
    MediaKind,
    MetricValue,
    Platform,
    PlaybookScope,
    PlaybookVersion,
    PollMetrics,
    Publish,
    PublishResult,
    ReadStats,
    RenderMedia,
    ResearchTrends,
    SourceResult,
    ToolError,
    TopicStat,
    TrendDigest,
    WritePlaybook,
)

DEFAULT_SOURCES = (
    "google_trends",
    "naver_search",
    "naver_datalab",
    "youtube_popular",
    "github_trending",
)

PLATFORM_METRIC_KEYS: dict[Platform, tuple[str, ...]] = {
    "instagram": ("reach", "likes", "shares", "saved", "comments", "avg_watch_time_ms", "views"),
    "youtube": (
        "views",
        "engaged_views",
        "avg_view_duration_s",
        "avg_view_pct",
        "likes",
        "comments",
        "shares",
        "subscribers_gained",
    ),
}


def _digest(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class FakeResearchTrends:
    """소스별 실패 격리(FR-G4): failing_sources는 ok=False로 포함되고 리서치는 계속."""

    def __init__(self, failing_sources: Iterable[str] = ()) -> None:
        self.failing_sources = set(failing_sources)

    def __call__(self, sources: tuple[str, ...] | None = None, limit: int = 10) -> TrendDigest:
        results = []
        for source in sources or DEFAULT_SOURCES:
            if source in self.failing_sources:
                results.append(SourceResult(source=source, ok=False))
            else:
                items = tuple(f"{source}-topic-{i}" for i in range(1, min(limit, 3) + 1))
                results.append(SourceResult(source=source, ok=True, items=items))
        lines = [f"- {item}" for r in results for item in r.items]
        return TrendDigest(
            digest_markdown="# 트렌드 다이제스트\n" + "\n".join(lines),
            source_results=tuple(results),
        )


class FakeRenderMedia:
    """같은 spec → 같은 checksum (FR-M1)."""

    def __call__(self, media_spec: Mapping[str, object], kind: MediaKind) -> MediaAsset:
        checksum = _digest({"spec": dict(media_spec), "kind": kind})
        return MediaAsset(kind=kind, storage_url=f"fake://media/{checksum}", checksum=checksum)


class FakePublish:
    """같은 idempotency_key 재호출 → 같은 결과, 이중 발행 0 (FR-P3)."""

    def __init__(self, error: ToolError | None = None) -> None:
        self.error = error
        self.results: dict[str, PublishResult] = {}
        self.calls: list[str] = []

    def __call__(
        self,
        platform: Platform,
        media: MediaAsset,
        caption: str,
        idempotency_key: str,
        container_id: str | None = None,
    ) -> PublishResult:
        self.calls.append(idempotency_key)
        if idempotency_key in self.results:
            return self.results[idempotency_key]
        if self.error is not None:
            result = PublishResult(error=self.error)
        else:
            key_hash = _digest([platform, media.checksum, idempotency_key])[:12]
            result = PublishResult(
                post_id=f"fake-post-{key_hash}",
                container_id=container_id or f"fake-container-{key_hash}",
            )
        self.results[idempotency_key] = result
        return result


class FakePollMetrics:
    """결정론 지표 + 결측 표현: missing_keys는 missing=True·value=None (FR-L1)."""

    def __init__(self, missing_keys: Iterable[str] = ("skip_rate",)) -> None:
        self.missing_keys = set(missing_keys)

    def __call__(
        self, platform: Platform, post_id: str, window_index: int
    ) -> tuple[MetricValue, ...]:
        values = []
        # 결측 키가 플랫폼 기본 키와 겹쳐도 metric_key가 중복되지 않게 dedup
        # (중복 시 DB UNIQUE(observation_id, metric_key)와 어긋남)
        base_keys = PLATFORM_METRIC_KEYS[platform]
        extra_missing = tuple(k for k in sorted(self.missing_keys) if k not in base_keys)
        for key in base_keys + extra_missing:
            if key in self.missing_keys:
                values.append(MetricValue(metric_key=key, value=None, missing=True))
            else:
                seed = int(_digest([platform, post_id, window_index, key])[:8], 16)
                values.append(MetricValue(metric_key=key, value=float(seed % 1000), missing=False))
        return tuple(values)


class FakeReadStats:
    def __init__(self, rows: Iterable[TopicStat] = ()) -> None:
        self.rows = tuple(rows)

    def __call__(self, platform: Platform | None = None) -> tuple[TopicStat, ...]:
        if platform is None:
            return self.rows
        return tuple(r for r in self.rows if r.platform == platform)


class FakeWritePlaybook:
    """scope별 버전 전진 — LLM 착지점 (FR-L4)."""

    def __init__(self) -> None:
        self.entries: dict[tuple[PlaybookScope, str | None], list[str]] = {}

    def __call__(
        self, scope: PlaybookScope, guidance: str, scope_ref: str | None = None
    ) -> PlaybookVersion:
        history = self.entries.setdefault((scope, scope_ref), [])
        history.append(guidance)
        return PlaybookVersion(scope=scope, scope_ref=scope_ref, version=len(history))


# 계약 적합성 정적 보증 — 가짜의 시그니처가 Protocol에서 드리프트하면 `mypy sns`가
# CI에서 실패한다. (테스트는 CI 타입체크 대상이 아니므로 여기서 sns 내부에 고정.)
_check_research: ResearchTrends = FakeResearchTrends()
_check_render: RenderMedia = FakeRenderMedia()
_check_publish: Publish = FakePublish()
_check_poll: PollMetrics = FakePollMetrics()
_check_stats: ReadStats = FakeReadStats()
_check_playbook: WritePlaybook = FakeWritePlaybook()
