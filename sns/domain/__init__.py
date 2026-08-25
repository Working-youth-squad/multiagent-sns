"""주제 도메인 팩 — 도메인이 바뀔 때 갈아끼우는 값을 한 곳에 모은다.

파이프라인 대부분(렌더·발행·게이트·알림)은 도메인 중립이지만, **무엇을 다루는
채널인가**는 네 자리에 흩어져 박혀 있었다: Topic 프롬프트, Content 프롬프트,
LLM 그라운딩 질의, 트렌드 소스 구성. 여기 모아서 한 값만 바꾸면 도메인이 바뀌게 한다.

**팩이 갖는 것과 갖지 않는 것.** 개념 그림의 *구조*(필드·검증·렌더)는
[sns.render.concept_image]에 남는다 — 그건 도메인이 아니라 그림꼴이다. 팩은 **어떤
종류를 쓸지와 프롬프트에 넣을 예시**만 갖는다. `compare`처럼 중립인 종류도 예시는
도메인 향이 강해서("list vs set", "O(n) -> O(1)") 함께 갈아야 하기 때문이다.

**주입은 기본값 있는 키워드 인자로 한다**(NFR-2 주입식 규율). 기본값이 DEVELOPER라
기존 호출부는 그대로 돌고, 다른 도메인은 명시로 넘긴다:

    run_topic(model, platform=..., domain=MARKETING)
"""

from collections.abc import Mapping

from sns.domain.developer import DEVELOPER
from sns.domain.pack import Domain, UnknownDomainError

# 등록된 팩. 도메인을 추가하면 모듈 하나 만들고 여기 한 줄 넣는다.
DOMAINS: Mapping[str, Domain] = {DEVELOPER.ref: DEVELOPER}

DEFAULT_DOMAIN: Domain = DEVELOPER


def resolve_domain(ref: str) -> Domain:
    """ref → 팩. 모르는 ref는 **거부한다**.

    조용히 기본값으로 떨어지면 오타 하나로 엉뚱한 도메인의 콘텐츠가 한 사이클치
    발행된다 — 되돌릴 수 없는 종류의 사고다.
    """
    try:
        return DOMAINS[ref]
    except KeyError:
        raise UnknownDomainError(
            f"모르는 도메인 ref: {ref!r} (등록된 것: {sorted(DOMAINS)})"
        ) from None


__all__ = [
    "DEFAULT_DOMAIN",
    "DEVELOPER",
    "DOMAINS",
    "Domain",
    "UnknownDomainError",
    "resolve_domain",
]
