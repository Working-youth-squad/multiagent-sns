"""`research_trends` 실구현 (FR-G4, 04-트렌드조사) — 무료 외부 트렌드 소스 조회.

오케스트레이션은 순수·네트워크 무관하다: 소스별 fetcher를 **주입**받아

- 각 소스를 독립 실행하고 예외/타임아웃은 **그 소스만** `ok=False`로 격리한다
  (FR-G4: 하나 죽어도 리서치는 계속). 미등록 소스 이름도 조용히 `ok=False`.
- 소스별 타임아웃(기본 10초, §2)을 스레드로 강제 — 한 소스의 지연이 전체를 막지 않게.
- 확인된(ok·비어있지 않은) 소스만 마크다운 다이제스트로 합친다 (FR-G4: 리서치로
  확인 안 된 내용은 소재에서 배제 → 할루시네이션 방지).

실 fetcher(네트워크)는 [sns.research.sources]에, 그 배선은 `default_service()`에.
"""

import os
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor

from sns.tools.contracts import ResearchTrends, SourceResult, TrendDigest

# 소스 fetcher: limit개 트렌드 항목(문자열)을 반환. 실패는 예외로 던진다 — 서비스가 격리.
SourceFetcher = Callable[[int], tuple[str, ...]]

DEFAULT_TIMEOUT_S = 10.0

# 인증 소스 자격증명 env 이름 (단일 출처).
ENV_NAVER_CLIENT_ID = "NAVER_CLIENT_ID"
ENV_NAVER_CLIENT_SECRET = "NAVER_CLIENT_SECRET"
ENV_YOUTUBE_API_KEY = "YOUTUBE_API_KEY"
ENV_GEMINI_API_KEY = "GEMINI_API_KEY"
# 그라운딩 모델을 코드 배포 없이 갈아끼우는 자리. 예전에 URL에 박힌 모델이 은퇴하면서
# 소스가 조용히 죽었고(404), 고치려면 배포가 필요했다
# ([sns.research.sources.llm_grounding.DEFAULT_MODEL]).
ENV_GROUNDING_MODEL = "GROUNDING_MODEL"


class ResearchTrendsService:
    """`ResearchTrends` 계약 구현. 주입된 fetcher 레지스트리 위에서 동작."""

    def __init__(
        self, fetchers: Mapping[str, SourceFetcher], *, timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> None:
        self._fetchers = dict(fetchers)
        self._timeout_s = timeout_s

    @property
    def sources(self) -> tuple[str, ...]:
        """등록된 소스 이름 — 어떤 소스가 살아 있는지 호출부·진단이 확인하는 지점."""
        return tuple(self._fetchers)

    def __call__(self, sources: tuple[str, ...] | None = None, limit: int = 10) -> TrendDigest:
        # 중복 소스명은 순서를 지키며 접는다. 접지 않으면 같은 엔드포인트에 요청이 두 번
        # 나가고, 결과에도 같은 소스가 두 번 실려 후보 하나가 "2개 소스가 봤다"로 집계된다
        # (등수 통계의 정렬 1순위가 present_count다). 호출자가 `--source x --source x`를
        # 줄 수 있는 이상 여기서 막는 게 맞다.
        selected = tuple(dict.fromkeys(sources if sources is not None else self._fetchers))
        if not selected:
            return TrendDigest(digest_markdown=_render_digest(()), source_results=())

        # 소스를 동시에 실행하되 결과는 selected 순서로 모은다(결정론).
        executor = ThreadPoolExecutor(max_workers=len(selected))
        try:
            futures = {source: executor.submit(self._invoke, source, limit) for source in selected}
            results = tuple(self._collect(source, futures[source], limit) for source in selected)
        finally:
            # 타임아웃난 fetcher를 기다리지 않는다 — urllib 자체 타임아웃으로 곧 종료된다.
            executor.shutdown(wait=False)
        return TrendDigest(digest_markdown=_render_digest(results), source_results=results)

    def _invoke(self, source: str, limit: int) -> tuple[str, ...]:
        fetcher = self._fetchers.get(source)
        if fetcher is None:
            raise KeyError(source)  # 미등록 → _collect가 ok=False로 격리
        return fetcher(limit)

    def _collect(self, source: str, future: Future[tuple[str, ...]], limit: int) -> SourceResult:
        try:
            # 타임아웃·미등록(KeyError)·네트워크/파싱 오류 모두 이 소스만 격리한다.
            items = future.result(timeout=self._timeout_s)
        except Exception:
            return SourceResult(source=source, ok=False)
        # fetcher가 과다 항목/공백을 줘도 정규화: 공백 제거 후 limit 상한.
        cleaned = tuple(item.strip() for item in items if item and item.strip())
        return SourceResult(source=source, ok=True, items=cleaned[:limit])


def _render_digest(results: tuple[SourceResult, ...]) -> str:
    lines = ["# 트렌드 다이제스트", ""]
    for r in results:
        # 실패/빈 소스는 조용히 제외 — 확인된 항목만 Topic Agent에 노출.
        if r.ok and r.items:
            lines.append(f"## {r.source}")
            lines.extend(f"- {item}" for item in r.items)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _bind(fetch: Callable[..., tuple[str, ...]], **bound: object) -> SourceFetcher:
    """키워드 인자(자격증명 등)를 미리 묶어 SourceFetcher(limit→items) 시그니처로 만든다."""

    def fetcher(limit: int) -> tuple[str, ...]:
        return fetch(limit, **bound)

    return fetcher


def default_service(
    timeout_s: float = DEFAULT_TIMEOUT_S,
    *,
    env: Mapping[str, str] | None = None,
    sources: Sequence[str] | None = None,
    search_terms: Sequence[str] | None = None,
    grounding_prompt: str | None = None,
    extra_fetchers: Mapping[str, SourceFetcher] | None = None,
) -> ResearchTrendsService:
    """사용 가능한 실 fetcher를 배선한 서비스. 새 소스가 붙을 때마다 여기 등록한다.

    무인증 소스(google_trends·github_trending·hacker_news·lobsters)는 항상 등록한다.
    인증 소스(네이버 2종·
    YouTube·LLM 그라운딩)는 env에 키가 있을 때만 등록 — 없으면 미등록이라 호출돼도
    ok=False로 격리된다(§2, 부분 가용성 허용). LLM 그라운딩은 선택이라 키가 있을
    때만 기본 소스에 합류한다.

    **온보딩 채널 주입점** (셋 다 기본 None = 기존 동작 무변경):

    - `sources`: 등록할 소스 이름을 좁힌다([sns.topic_policy.trend_sources_for]).
      개발 전용 소스를 요리 채널에 남겨두면 그 트렌드에서 주제를 골라 대본이 억지가
      된다. **`__call__`의 `sources`가 아니라 등록 시점에 거른다** — `run_topic`은
      소스를 지정하지 않고 부르므로 등록된 전부가 대상이 된다.
    - `search_terms`: 검색형 소스의 질의어. 첫 항목이 네이버 검색의 대표 질의어,
      전체가 데이터랩의 추이 비교 키워드다. 프로필의 `(topic_major, *topic_subs)`가
      그대로 들어온다 — 사람이 인터뷰에서 고른 말이 곧 검색어다.
    - `grounding_prompt`: LLM 그라운딩 질의([sns.topic_policy.grounding_prompt_for]).
    - `extra_fetchers`: 호출자가 이름 붙여 넘기는 소스. 질의어 키워드 랭킹처럼 **내장
      레지스트리에 없는** 소스를 얹는 자리다([sns.onboarding.trends]). `sources` 필터를
      타지 않는다 — 명시로 준 것이라 내장 목록에 있을 리 없고, 태우면 전부 걸러진다.

    그라운딩 **모델**은 env `GROUNDING_MODEL`로 바꾼다 — 프로필이 아니라 운영 설정이라
    자격증명과 같은 자리에서 읽는다. 모델이 은퇴해도 코드 배포 없이 넘어갈 수 있어야
    한다(예전에 URL에 박힌 모델이 은퇴해 소스가 조용히 죽었다).
    """
    from sns.research.sources.devnews import fetch_hacker_news, fetch_lobsters
    from sns.research.sources.github_trending import fetch_github_trending
    from sns.research.sources.google_trends import fetch_google_trends
    from sns.research.sources.llm_grounding import fetch_llm_grounding, gemini_url
    from sns.research.sources.naver_datalab import fetch_naver_datalab
    from sns.research.sources.naver_search import fetch_naver_search
    from sns.research.sources.youtube_popular import fetch_youtube_popular

    env_map: Mapping[str, str] = os.environ if env is None else env
    fetchers: dict[str, SourceFetcher] = {
        "google_trends": fetch_google_trends,
        "github_trending": fetch_github_trending,
        # 개발자 뉴스 2종 — 키 불필요, 회전이 빠르다([sns.research.sources.devnews]).
        "hacker_news": fetch_hacker_news,
        "lobsters": fetch_lobsters,
    }

    naver_id = env_map.get(ENV_NAVER_CLIENT_ID)
    naver_secret = env_map.get(ENV_NAVER_CLIENT_SECRET)
    if naver_id and naver_secret:
        # 질의어를 안 넘기면 fetcher의 개발 기본값이 쓰인다 — 소스만 갈아끼우면
        # 도메인을 바꿔도 같은 걸 검색한다.
        terms = tuple(search_terms or ())
        query = {"query": terms[0]} if terms else {}
        keywords = {"keywords": terms} if terms else {}
        fetchers["naver_search"] = _bind(
            fetch_naver_search, client_id=naver_id, client_secret=naver_secret, **query
        )
        fetchers["naver_datalab"] = _bind(
            fetch_naver_datalab, client_id=naver_id, client_secret=naver_secret, **keywords
        )

    youtube_key = env_map.get(ENV_YOUTUBE_API_KEY)
    if youtube_key:
        fetchers["youtube_popular"] = _bind(fetch_youtube_popular, api_key=youtube_key)

    gemini_key = env_map.get(ENV_GEMINI_API_KEY)
    if gemini_key:
        # 안 넘긴 값은 바인딩에서 아예 뺀다 — fetcher의 기본값이 살아 있게.
        bound: dict[str, object] = {"api_key": gemini_key}
        if grounding_prompt:
            bound["prompt"] = grounding_prompt
        grounding_model = env_map.get(ENV_GROUNDING_MODEL)
        if grounding_model:
            bound["url"] = gemini_url(grounding_model)
        fetchers["llm_grounding"] = _bind(fetch_llm_grounding, **bound)

    if sources is not None:
        # 이 주제가 안 쓰는 소스는 키가 있어도 등록하지 않는다.
        fetchers = {k: v for k, v in fetchers.items() if k in set(sources)}
    if extra_fetchers:
        # **`sources` 필터를 태우지 않는다.** 호출자가 이름을 붙여 명시로 넘긴 것이라
        # 내장 소스 목록에 있을 리가 없다 — 태우면 방금 준 것이 전부 걸러진다.
        fetchers.update(extra_fetchers)
    return ResearchTrendsService(fetchers, timeout_s=timeout_s)


# mypy(sns): 서비스가 동결 계약 ResearchTrends를 구조적으로 만족함을 강제.
_check_service: ResearchTrends = ResearchTrendsService({})
