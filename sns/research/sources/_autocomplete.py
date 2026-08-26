"""검색창 자동완성 소스 공용부 — 연관어 추출 규율 (04-트렌드조사 §2 [신설]).

네이버·구글·유튜브 자동완성은 응답 **형식만** 다르고 그 뒤 처리는 같다: 문자열로 정리하고,
질의어 자신을 빼고, 순서를 유지한다. 그 공통부를 여기 한 번만 둔다.

자기 자신 제외를 공백·대소문자에 둔감하게 하는 이유: 실제 응답에는 질의어가 그대로 섞여
들어오고("개발자"), 띄어쓰기만 다른 변형("개발 자")도 같은 말이다. 완전 일치로만 빼면
그 변형이 등수 한 칸을 차지해 소스별 백분위(ranking)가 흔들린다.
"""

import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping

from sns.net.http import USER_AGENT
from sns.research.keytext import squeezed

__all__ = ["USER_AGENT", "clamp_limit", "get_request", "related_terms"]
"""USER_AGENT 재수출: 이 모듈이 UA를 거는 지점이라 테스트·호출부가 여기서 읽어 왔다."""


def related_terms(query: str, candidates: Iterable[object]) -> tuple[str, ...]:
    """후보에서 질의어 자신과 빈 항목을 뺀 연관어를 순서대로.

    **비-`str` 원소는 문자열화하지 않고 건너뛴다.** `str(candidate)`를 걸면
    `["등산화", 0, [1]]` 같은 변종 응답이 `"['등산화', 0, [1]]"`이라는 **파이썬 리터럴
    문자열**로 키워드 목록에 들어가, 등수 통계를 거쳐 챗봇 응답과 LLM 프롬프트까지
    그대로 간다. 형식이 바뀐 것을 파서가 예외로 알려야 소스 격리(FR-G4)가 작동할
    기회가 생긴다 — 그 판정은 원소 타입을 아는 각 파서 몫이다(suggest.parse_suggest).
    `parse_naver_autocomplete`가 이미 쓰던 규율을 공용부로 올린 것이다.
    """
    query_key = squeezed(query)
    out: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        text = candidate.strip()
        if text and squeezed(text) != query_key:
            out.append(text)
    return tuple(out)


def clamp_limit(limit: int, *, maximum: int | None = None) -> int:
    """limit을 1 이상으로 강제. 0/음수는 빈 랭킹을 '정상 관측'으로 만들어 등수를 오염시킨다."""
    value = max(1, limit)
    return value if maximum is None else min(value, maximum)


def get_request(url: str, params: Mapping[str, str]) -> urllib.request.Request:
    """쿼리를 붙이고 UA를 단 GET Request.

    **UA가 없으면 구글 자동완성이 EUC-KR로 응답한다**(실측 2026-08-25:
    UA 없음 → `charset=EUC-KR`, UA 있음 → `charset=UTF-8`). 그대로 `json.loads`하면
    `UnicodeDecodeError`로 소스가 통째로 죽는데, 서비스가 격리해 버리기 때문에 원인이
    보이지 않는다. 호출부는 `oe=utf-8`도 함께 보내 UA 스니핑에 기대지 않는다.
    """
    return urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": USER_AGENT}
    )
