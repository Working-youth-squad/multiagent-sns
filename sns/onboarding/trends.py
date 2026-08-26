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
from sns.research.trends import DEFAULT_TIMEOUT_S, ResearchTrendsService, default_service
from sns.topic_policy import grounding_prompt_for, trend_sources_for


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
    )
