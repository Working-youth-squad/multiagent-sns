"""트렌드 조사 (C1, FR-G4) — `research_trends` 실구현 + 무료 외부 소스 fetcher.

두 갈래가 있다:

- **고정 피드** (`trends`) — google_trends·github_trending·hacker_news·… 를 긁어
  마크다운 다이제스트로 합친다. Topic 에이전트가 쓰는 기존 경로.
- **질의어 기반** (`keywords`·`ranking`) — 사용자가 넣은 키워드로 자동완성 3소스를 긁어
  등수 통계·표준편차 밴드로 근거 있는 후보 목록을 만든다. 챗봇 경로.
"""

from sns.research.keywords import (
    DEFAULT_LIMIT,
    DEFAULT_TOP,
    KEYWORD_SOURCES,
    aggregate,
    keyword_service,
    rank_keywords,
    ranking_to_dict,
)
from sns.research.ranking import (
    BAND_PERCENTILES,
    MISSING_PCT_RANK,
    KeywordRanking,
    KeywordStat,
    SourceRank,
)
from sns.research.trends import (
    DEFAULT_TIMEOUT_S,
    ResearchTrendsService,
    SourceFetcher,
    default_service,
)

__all__ = [
    "BAND_PERCENTILES",
    "DEFAULT_LIMIT",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_TOP",
    "KEYWORD_SOURCES",
    "MISSING_PCT_RANK",
    "KeywordRanking",
    "KeywordStat",
    "ResearchTrendsService",
    "SourceFetcher",
    "SourceRank",
    "aggregate",
    "default_service",
    "keyword_service",
    "rank_keywords",
    "ranking_to_dict",
]
