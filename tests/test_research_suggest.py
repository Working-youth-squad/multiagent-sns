"""자동완성 소스 3종 — 파서·요청 구성·상한 (04-트렌드조사 §2 [신설]).

레포 관례대로 `opener`에 가짜를 주입해 네트워크 없이 돈다. 요청 URL·타임아웃·읽기 상한은
sink로 캡처해 단언한다 — 소스별 10초 상한과 파싱 DoS 방어가 실제로 걸리는지 보는 지점.
"""

import json
from typing import Any

import pytest

from sns.net.http import MAX_RESPONSE_BYTES
from sns.research.sources._autocomplete import USER_AGENT, clamp_limit, get_request, related_terms
from sns.research.sources.naver_autocomplete import (
    fetch_naver_autocomplete,
    parse_naver_autocomplete,
)
from sns.research.sources.suggest import (
    fetch_google_suggest,
    fetch_youtube_suggest,
    parse_suggest,
)

_GOOGLE = json.dumps(
    ["개발자", ["개발자 연봉", "개발자 로드맵", "  ", "개발자", "개발 자", "개발자 취업"]]
).encode()

# 실측 형태(2026-08-25 ac.search.naver.com/nx/ac): items = [[ ["연관어"], … ]] — 그룹 하나에
# 항목 리스트가 들어 있고 각 항목의 [0]이 문자열이다. 질의어 자신("개발자")도 섞여 온다.
_NAVER = json.dumps(
    {
        "query": ["개발자"],
        "items": [[["개발자 연봉"], ["개발자옵션"], ["개발자"], ["백엔드 개발자"]]],
    }
).encode()


class _FakeResponse:
    def __init__(self, data: bytes, sink: dict[str, Any]) -> None:
        self._data = data
        self._sink = sink

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, amt: int = -1, /) -> bytes:
        self._sink["read_amt"] = amt
        return self._data


def _opener(data: bytes, sink: dict[str, Any] | None = None):  # noqa: ANN202 — 테스트 헬퍼
    box = sink if sink is not None else {}

    def opener(target: object, timeout: float | None = None):  # noqa: ANN202
        box["target"] = target
        box["timeout"] = timeout
        return _FakeResponse(data, box)

    return opener


# ── 공용부 ───────────────────────────────────────────────────────────


def test_related_terms_drops_query_itself_and_blanks() -> None:
    assert related_terms("개발자", ["개발자", "  ", "개발 자", "개발자 연봉"]) == ("개발자 연봉",)


def test_related_terms_keeps_order() -> None:
    assert related_terms("x", ["가", "나", "다"]) == ("가", "나", "다")


@pytest.mark.parametrize(("raw", "expected"), [(0, 1), (-5, 1), (3, 3)])
def test_clamp_limit_forces_at_least_one(raw: int, expected: int) -> None:
    """limit=0이 빈 랭킹을 '정상 관측'으로 만들면 등수 분모가 오염된다."""
    assert clamp_limit(raw) == expected


# ── 구글·유튜브 ──────────────────────────────────────────────────────


def test_parse_suggest_extracts_related_terms() -> None:
    assert parse_suggest(_GOOGLE) == ("개발자 연봉", "개발자 로드맵", "개발자 취업")


@pytest.mark.parametrize(
    "payload",
    [b"{}", b"[]", b'["only"]', b'["q", "not-a-list"]'],
)
def test_parse_suggest_rejects_wrong_shape(payload: bytes) -> None:
    with pytest.raises(ValueError, match="형식"):
        parse_suggest(payload)


def test_google_suggest_request_and_limit() -> None:
    sink: dict[str, Any] = {}
    items = fetch_google_suggest(2, query="개발자", timeout_s=3.0, opener=_opener(_GOOGLE, sink))
    assert items == ("개발자 연봉", "개발자 로드맵")
    assert "q=%EA%B0%9C%EB%B0%9C%EC%9E%90" in sink["target"].full_url
    assert "ds=" not in sink["target"].full_url  # 구글은 ds 없음
    assert sink["timeout"] == 3.0
    assert sink["read_amt"] == MAX_RESPONSE_BYTES


def test_youtube_suggest_uses_ds_yt() -> None:
    sink: dict[str, Any] = {}
    fetch_youtube_suggest(5, query="개발자", opener=_opener(_GOOGLE, sink))
    assert "ds=yt" in sink["target"].full_url


def test_suggest_clamps_zero_limit() -> None:
    assert len(fetch_google_suggest(0, query="개발자", opener=_opener(_GOOGLE))) == 1


# ── 네이버 ───────────────────────────────────────────────────────────


def test_parse_naver_autocomplete_flattens_nested_lists() -> None:
    got = parse_naver_autocomplete(_NAVER, query="개발자")
    assert got == ("개발자 연봉", "개발자옵션", "백엔드 개발자")


@pytest.mark.parametrize("payload", [b"[]", b'{"no_items": 1}'])
def test_parse_naver_rejects_wrong_shape(payload: bytes) -> None:
    with pytest.raises(ValueError, match="형식|items"):
        parse_naver_autocomplete(payload)


def test_naver_autocomplete_request_carries_query() -> None:
    sink: dict[str, Any] = {}
    items = fetch_naver_autocomplete(2, query="개발자", timeout_s=7.0, opener=_opener(_NAVER, sink))
    assert items == ("개발자 연봉", "개발자옵션")
    assert "r_format=json" in sink["target"].full_url
    assert sink["timeout"] == 7.0


def test_naver_autocomplete_is_unauthenticated() -> None:
    """네이버 검색 API 키와 무관하다 — 시그니처에 자격증명 인자가 없다."""
    import inspect

    params = inspect.signature(fetch_naver_autocomplete).parameters
    assert "client_id" not in params and "client_secret" not in params


# ── 인코딩 회귀 (2026-08-25 실측) ─────────────────────────────────────


def test_request_carries_user_agent() -> None:
    """UA가 없으면 구글 자동완성이 EUC-KR로 응답해 json.loads가 죽는다.

    실측: UA 없음 → `Content-Type: charset=EUC-KR`, UA 있음 → `charset=UTF-8`.
    서비스가 소스를 격리해 버리므로 이 회귀는 조용히 '소스 실패'로만 보인다.
    """
    request = get_request("https://example.test/x", {"q": "개발자"})
    assert request.get_header("User-agent") == USER_AGENT


@pytest.mark.parametrize(
    ("fetch", "kwargs"),
    [(fetch_google_suggest, {}), (fetch_youtube_suggest, {})],
)
def test_suggest_requests_utf8_output(fetch, kwargs) -> None:  # noqa: ANN001 — 파라미터화 헬퍼
    """UA 스니핑에만 기대지 않는다 — oe=utf-8로 인코딩을 명시적으로 못 박는다."""
    sink: dict[str, Any] = {}
    fetch(3, query="개발자", opener=_opener(_GOOGLE, sink), **kwargs)
    assert "oe=utf-8" in sink["target"].full_url


def test_naver_autocomplete_carries_user_agent() -> None:
    sink: dict[str, Any] = {}
    fetch_naver_autocomplete(3, query="개발자", opener=_opener(_NAVER, sink))
    assert sink["target"].get_header("User-agent") == USER_AGENT
