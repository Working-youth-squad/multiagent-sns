"""트렌드 조사 (C1, FR-G4) — `research_trends` 실구현 + 무료 외부 소스 fetcher."""

from sns.research.trends import (
    DEFAULT_TIMEOUT_S,
    ResearchTrendsService,
    SourceFetcher,
    default_service,
)

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "ResearchTrendsService",
    "SourceFetcher",
    "default_service",
]
