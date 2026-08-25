"""팩 계약 — `Domain` 데이터클래스와 오류 타입.

팩 자체(`DEVELOPER` 등)와 분리한 이유: 팩 모듈이 이 계약을 import 하는데, 계약이
`sns.domain.__init__`에 있으면 순환 import가 된다.
"""

from collections.abc import Mapping
from dataclasses import dataclass


class UnknownDomainError(KeyError):
    """등록되지 않은 도메인 ref — 조용한 기본값 폴백을 막는다."""


@dataclass(frozen=True)
class Domain:
    """한 주제 도메인이 파이프라인에 주입하는 값 전부.

    전부 문자열·튜플이다. 로직이 들어오면 팩이 아니라 모듈로 빼야 한다는 신호다.
    """

    ref: str
    """`resolve_domain`이 쓰는 식별자. 소문자 영문."""

    audience: str
    """프롬프트에 삽입될 대상 서술 — "개발자 대상" 같은 짧은 구."""

    topic_domain: str
    """주제 범위 한 줄 — Content 프롬프트가 "주어진 {topic_domain} 주제로"에 쓴다."""

    categories: tuple[str, ...]
    """Topic 에이전트가 고를 카테고리. 성과 분석의 축이 되므로 함부로 늘리지 않는다."""

    grounding_prompt: str
    """LLM 그라운딩 소스가 검색과 함께 던질 질의([sns.research.sources.llm_grounding])."""

    trend_sources: tuple[str, ...]
    """이 도메인이 쓸 트렌드 소스 키. `default_service`의 등록 이름과 같아야 한다."""

    concept_kinds: tuple[str, ...]
    """이 도메인이 허용할 개념 그림 종류. [sns.render.concept_image]의 이름을 쓴다."""

    concept_examples: Mapping[str, str]
    """kind → 프롬프트에 넣을 예시 블록. 키는 `concept_kinds`와 정확히 일치해야 한다."""
