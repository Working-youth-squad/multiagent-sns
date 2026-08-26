"""프로필 → 트렌드 서비스 조립 — 온보딩 추천과 사이클의 **단일 출처**.

같은 조립이 두 곳에서 필요하다:

- 온보딩 화면 6([sns.onboarding.recommend.default_trend_provider]) — 추천안의 근거
- 프로필 채널 사이클(`scripts/run_profile_cycle.py`) — 주제 후보

한쪽에만 쓰면 **요리 채널이 개발 트렌드를 근거로 채널 이름을 추천받는다**(화면 6이 오래
그랬다 — `profile`을 인자로 받아놓고 쓰지 않았다). 두 벌로 복사하면 한쪽만 고쳐지는 날이
온다. 그래서 한 함수로 둔다.

무엇이 어디서 오는지는 [sns.topic_policy]와 같은 규율이다: **소스 목록은 코드 파생**
(이름이 `default_service`의 등록 키와 정확히 같아야 한다), **질의어는 프로필에서 직접**
(사람이 인터뷰에서 고른 말이 곧 검색어다).
"""

from collections.abc import Mapping

from sns.onboarding.profile import ChannelProfile
from sns.research.keywords import rank_keywords
from sns.research.trends import (
    DEFAULT_TIMEOUT_S,
    ResearchTrendsService,
    SourceFetcher,
    default_service,
)
from sns.topic_policy import grounding_prompt_for, trend_sources_for

KEYWORD_SOURCE_PREFIX = "keywords:"
"""세부 주제별 키워드 소스의 이름 앞머리 — 다이제스트에서 어느 니치의 후보인지 보인다."""


def _keyword_fetcher(query: str, timeout_s: float) -> SourceFetcher:
    """질의어 1개 → 자동완성 3종 교차검증 후보([sns.research.keywords]).

    `SourceFetcher`(limit→items) 모양으로 감싸면 기존 서비스가 동시 실행·소스별
    타임아웃·실패 격리를 그대로 해준다 — 어댑터나 합성 클래스가 필요 없다.
    """

    def fetcher(limit: int) -> tuple[str, ...]:
        ranking = rank_keywords(query, top=limit, timeout_s=timeout_s)
        return tuple(stat.text for stat in ranking.candidates)

    return fetcher


def profile_trend_service(
    profile: ChannelProfile,
    *,
    env: Mapping[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> ResearchTrendsService:
    """이 채널의 주제에 맞춘 트렌드 서비스.

    `env`는 그대로 넘긴다 — 테스트가 실 키·네트워크 없이 소스 구성만 확인하는 지점이다
    (`default_service`가 이미 쓰는 seam).
    """
    return default_service(
        timeout_s,
        env=env,
        sources=trend_sources_for(profile.topic_major),
        search_terms=(profile.topic_major, *profile.topic_subs),
        grounding_prompt=grounding_prompt_for(profile.topic_major, profile.topic_subs),
        # 세부 주제마다 소스 하나. 자동완성은 질의어가 구체적일수록 쓸모 있는 연관어를
        # 낸다 — 대분류("요리")는 일반명사만 나오기 쉽다. 무인증이라 호출 비용이 0이고,
        # 니치별로 나눠 두면 Topic 에이전트가 어디서 온 후보인지 구분해서 본다.
        extra_fetchers={
            f"{KEYWORD_SOURCE_PREFIX}{sub}": _keyword_fetcher(sub, timeout_s)
            for sub in profile.topic_subs
        },
    )
