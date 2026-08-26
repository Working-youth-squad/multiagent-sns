"""질의어 → 근거 있는 트렌드 키워드 목록 (04-트렌드조사 §5 [신설]).

**단일 진입점은 `rank_keywords(query)`다.** 사용자가 "개발자"를 넣으면 네이버·구글·유튜브
자동완성 3소스를 동시에 긁어 등수 통계를 내고, 표준편차 밴드로 거른 뒤 근거 강도순으로
돌려준다. 통계 정의는 [sns.research.ranking] 참조.

기존 `research_trends`와의 관계: 오케스트레이션(동시 실행·소스별 타임아웃·실패 격리)은
`ResearchTrendsService`를 그대로 재사용한다. 다른 점은 **소스가 질의어를 받는다**는 것뿐이라,
`keyword_service(query)`가 질의어를 fetcher에 묶어 넣는다(`trends._bind`와 같은 수법).
고정 피드 소스(google_trends 등)는 질의어를 받지 못하므로 여기 등록하지 않는다.

네 가지가 인자로 꺼져 있다 — 챗봇/LLM 프롬프트 팀이 고르는 지점이다:

| 토글 | 인자 | 끄면 |
|---|---|---|
| 소스별 | `sources=("google_suggest",)` | 지정한 소스만 |
| 밴드 필터 | `band=False` / `percentiles=(10, 90)` | 전 후보 반환(filter_mode="off") |
| 교차검증 하한 | `min_present=2` | 2개 이상 소스가 아는 키워드만 |
| 제외 키워드 | `exclude=[...]` (기본 없음) | 걸러내지 않는다 |
| 제외 매칭 폭 | `exclude_ignore_spaces=True` | 공백 무시 부분일치(오탐 위험, 기본 off) |

`exclude`가 기본으로 비어 있는 이유: 이 모듈은 **관련성**을 판정하지 않는다. 등수 분산만
보므로 밈·오타도 통과한다. 무엇이 부적절한지는 도메인이 정하는 문제라, 질의어에서 부정
프롬프트를 파생시키는 쪽이 그 목록을 여기로 주입한다.
"""

from collections.abc import Sequence

from sns.research.ranking import (
    BAND_PERCENTILES,
    MIN_BAND_POOL,
    MIN_BAND_SOURCES,
    KeywordRanking,
    KeywordStat,
    band_bounds,
    excluded_match,
    live_results,
    rank_stats,
)
from sns.research.sources.naver_autocomplete import fetch_naver_autocomplete
from sns.research.sources.suggest import fetch_google_suggest, fetch_youtube_suggest
from sns.research.trends import DEFAULT_TIMEOUT_S, ResearchTrendsService, SourceFetcher
from sns.tools.contracts import SourceResult

KEYWORD_SOURCES: tuple[str, ...] = (
    "naver_autocomplete",
    "google_suggest",
    "youtube_suggest",
)
"""질의어를 그대로 받는 소스 — 자동완성 3종. 전부 무인증이라 키 없이 항상 등록된다.

CLI `--source` choices와 문서용 상수다. `rank_keywords(sources=None)`은 이 상수가 아니라
**주입된 서비스의 레지스트리**로 폴백한다 — 그래야 `service=`로 임의 소스를 넣을 수 있다.
"""

DEFAULT_LIMIT = 20
"""소스별 수집 깊이. 자동완성 엔드포인트는 실측상 9~10개만 주므로 상한 역할이다."""

DEFAULT_TOP = 10
"""사용자에게 보여줄 후보 상한."""


def _bind_query(fetch: object, query: str, timeout_s: float) -> SourceFetcher:
    """질의어·타임아웃을 미리 묶어 SourceFetcher(limit→items) 시그니처로 만든다."""

    def fetcher(limit: int) -> tuple[str, ...]:
        return fetch(limit, query=query, timeout_s=timeout_s)  # type: ignore[operator,no-any-return]

    return fetcher


def keyword_service(query: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> ResearchTrendsService:
    """질의어를 묶은 자동완성 3종 서비스. 실패 격리·타임아웃은 기존 서비스가 그대로 한다."""
    if not query.strip():
        raise ValueError("질의어가 비어 있다")
    return ResearchTrendsService(
        {
            "naver_autocomplete": _bind_query(fetch_naver_autocomplete, query, timeout_s),
            "google_suggest": _bind_query(fetch_google_suggest, query, timeout_s),
            "youtube_suggest": _bind_query(fetch_youtube_suggest, query, timeout_s),
        },
        timeout_s=timeout_s,
    )


def aggregate(
    query: str,
    results: Sequence[SourceResult],
    *,
    band: bool = True,
    percentiles: tuple[float, float] = BAND_PERCENTILES,
    min_present: int = 1,
    exclude: Sequence[str] | None = None,
    exclude_ignore_spaces: bool = False,
    top: int = DEFAULT_TOP,
) -> KeywordRanking:
    """이미 수집된 소스 결과 → 통계·필터·정렬. **순수 함수**(네트워크 접촉 없음).

    수집과 분리해 두는 이유는 재현이다 — 같은 입력이면 언제나 같은 표가 나와야 밴드
    경계를 두고 이야기할 수 있다.
    """
    if min_present < 1:
        raise ValueError(f"min_present는 1 이상이어야 한다: {min_present}")

    stats = rank_stats(results)

    excluded: list[tuple[str, str]] = []
    if exclude:
        survivors: list[KeywordStat] = []
        for stat in stats:
            # 대표 표기가 아니라 **관측된 표기 전부**로 판정한다 — 그래야 소스 나열
            # 순서가 제외 여부를 가르지 않는다. 기록도 실제로 걸린 표기를 남긴다.
            match = excluded_match(stat, exclude, ignore_spaces=exclude_ignore_spaces)
            if match is None:
                survivors.append(stat)
            else:
                excluded.append(match)
        stats = tuple(survivors)

    if min_present > 1:
        stats = tuple(s for s in stats if s.present_count >= min_present)

    live = live_results(results)
    live_sources = len(live)
    scored = tuple(s for s in stats if s.rank_std is not None)
    unscored = tuple(s for s in stats if s.rank_std is None)

    mode = "active"
    bounds: tuple[float, float] | None = None
    dropped: tuple[KeywordStat, ...] = ()
    if not band:
        mode, reason = "off", "호출자가 밴드 필터를 껐다"
    elif live_sources < MIN_BAND_SOURCES:
        mode = "passthrough"
        reason = f"참여 소스 {live_sources}개 < {MIN_BAND_SOURCES} — rank_std가 정의될 수 없다"
    elif len(scored) < MIN_BAND_POOL:
        mode = "passthrough"
        reason = (
            f"rank_std가 정의된 후보 {len(scored)}개 < {MIN_BAND_POOL}"
            f" (전체 {len(stats)}개 중) — 퍼센타일이 양끝 원소가 된다"
        )
    else:
        low, high = band_bounds([s.rank_std for s in scored if s.rank_std is not None], percentiles)
        bounds = (low, high)
        dropped = tuple(
            s for s in scored if s.rank_std is not None and not (low <= s.rank_std <= high)
        )
        reason = (
            f"rank_std {low:.4f}~{high:.4f}"
            f" ({percentiles[0]:g}~{percentiles[1]:g}퍼센타일)"
            f" · 판정 대상 {len(scored)}개 · 미판정 {len(unscored)}개"
        )

    ok_names = {r.source for r in live}
    dropped_ids = {id(s) for s in dropped}
    kept = tuple(s for s in stats if id(s) not in dropped_ids)
    return KeywordRanking(
        query=query,
        filter_mode=mode,  # type: ignore[arg-type]
        band=bounds,
        reason=reason,
        sources_ok=tuple(r.source for r in live),
        sources_failed=tuple(
            dict.fromkeys(r.source for r in results if not r.ok and r.source not in ok_names)
        ),
        candidates=kept[:top],
        pool=tuple(stats),
        dropped=dropped,
        unscored=unscored,
        excluded=tuple(excluded),
    )


def rank_keywords(
    query: str,
    *,
    sources: Sequence[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    top: int = DEFAULT_TOP,
    band: bool = True,
    percentiles: tuple[float, float] = BAND_PERCENTILES,
    min_present: int = 1,
    exclude: Sequence[str] | None = None,
    exclude_ignore_spaces: bool = False,
    service: ResearchTrendsService | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> KeywordRanking:
    """질의어 1개 → 근거 있는 트렌드 키워드 목록. 챗봇/LLM 팀의 유일한 진입점.

    소스 1개가 죽어도 나머지로 완주한다(FR-G4). `service`를 주면 그것을 쓴다 —
    테스트가 네트워크 없이 도는 지점이다.
    """
    active = service or keyword_service(query, timeout_s=timeout_s)
    # sources=None은 **서비스가 자기 레지스트리로 폴백**한다(trends.__call__). 여기서
    # KEYWORD_SOURCES를 강제하면 3종 이외의 이름으로 소스를 등록한 서비스를 주입했을 때
    # 전부 미등록으로 격리돼 후보가 0건이 된다 — docstring이 광고하는 `service=` 주입
    # 지점이 그 상수 때문에 무력해진다. KEYWORD_SOURCES는 CLI choices·문서용 상수로만 둔다.
    selected = tuple(sources) if sources is not None else None
    digest = active(selected, limit=limit)
    return aggregate(
        query,
        digest.source_results,
        band=band,
        percentiles=percentiles,
        min_present=min_present,
        exclude=exclude,
        exclude_ignore_spaces=exclude_ignore_spaces,
        top=top,
    )


def ranking_to_dict(ranking: KeywordRanking) -> dict[str, object]:
    """JSON 직렬화용 dict — 프로세스 경계(챗봇이 다른 언어일 때)가 쓰는 모양.

    `rank_std`는 `None`을 그대로 null로 낸다. 0으로 채우면 "불일치 없음"으로 오독된다.
    """

    def stat(s: KeywordStat) -> dict[str, object]:
        return {
            "text": s.text,
            "present_count": s.present_count,
            "observed_mean": round(s.observed_mean, 6),
            "rank_std": None if s.rank_std is None else round(s.rank_std, 6),
            "per_source": {
                r.source: {"pct_rank": round(r.pct_rank, 6), "present": r.present}
                for r in s.per_source
            },
        }

    return {
        "query": ranking.query,
        "filter_mode": ranking.filter_mode,
        "band": list(ranking.band) if ranking.band else None,
        "reason": ranking.reason,
        "sources_ok": list(ranking.sources_ok),
        "sources_failed": list(ranking.sources_failed),
        "candidates": [stat(s) for s in ranking.candidates],
        "dropped": [stat(s) for s in ranking.dropped],
        "unscored": [s.text for s in ranking.unscored],
        "excluded": [{"text": t, "keyword": k} for t, k in ranking.excluded],
    }
