"""인스타그램 인사이트 폴러 — 키 매핑·정직 결측·오류 전파. 네트워크 0."""

from typing import Any

import pytest

from sns.adapters.instagram.metrics import (
    MEDIA_INSIGHTS_METRICS,
    REELS_ONLY_KEYS,
    InstagramMetrics,
)
from sns.adapters.instagram.publisher import GraphError
from sns.tools.fakes import PLATFORM_METRIC_KEYS

_UNIVERSAL = tuple(k for k in MEDIA_INSIGHTS_METRICS if k not in REELS_ONLY_KEYS)


def _entry(name: str, value: Any) -> dict[str, Any]:
    return {"name": name, "period": "lifetime", "values": [{"value": value}]}


def _payload(values: dict[str, Any]) -> dict[str, Any]:
    """표준 키 → 값 dict를 Graph insights 응답 모양으로."""
    return {"data": [_entry(MEDIA_INSIGHTS_METRICS[k], v) for k, v in values.items()]}


class _FakeGraph:
    """`GraphHttp` 가짜 — 요청한 metric 문자열로 응답을 고른다."""

    def __init__(self, *, on_get: Any) -> None:
        self._on_get = on_get
        self.gets: list[tuple[str, dict[str, str]]] = []

    def get(self, path: str, params: Any) -> dict[str, Any]:
        self.gets.append((path, dict(params)))
        result = self._on_get(tuple(params["metric"].split(",")))
        if isinstance(result, Exception):
            raise result
        return result

    def post(self, path: str, params: Any) -> dict[str, Any]:  # 폴러는 쓰지 않는다
        raise AssertionError("폴러가 POST를 보냈다")


def _graph_error(*, code: int | None, subcode: int | None = None, status: int = 400) -> GraphError:
    return GraphError(http_status=status, code=code, subcode=subcode, raw="{}")


def _poller(on_get: Any) -> tuple[InstagramMetrics, _FakeGraph]:
    fake = _FakeGraph(on_get=on_get)
    return InstagramMetrics(fake, access_token="tok"), fake


def _all_present(_: tuple[str, ...]) -> dict[str, Any]:
    return _payload(
        {
            "reach": 1200,
            "likes": 90,
            "shares": 14,
            "saved": 22,
            "comments": 5,
            "avg_watch_time_ms": 8400,
            "views": 3100,
        }
    )


def test_maps_insights_to_standard_keys() -> None:
    poller, fake = _poller(_all_present)
    by_key = {v.metric_key: v for v in poller("instagram", "17895695668004550", 0)}

    assert by_key["reach"].value == 1200.0
    assert by_key["shares"].value == 14.0
    assert by_key["avg_watch_time_ms"].value == 8400.0
    assert all(not v.missing for v in by_key.values())
    path, params = fake.gets[0]
    assert path == "/17895695668004550/insights"
    assert params["access_token"] == "tok"


def test_reels_metric_asked_separately() -> None:
    # 릴스 전용 지표가 공용 배치에 섞이면 이미지 게시물에서 배치 전체가 죽는다.
    poller, fake = _poller(_all_present)
    poller("instagram", "media-1", 0)

    assert len(fake.gets) == 2
    universal, reels = (set(params["metric"].split(",")) for _, params in fake.gets)
    assert "ig_reels_avg_watch_time" not in universal
    assert reels == {"ig_reels_avg_watch_time"}


def test_metric_keys_consistent_with_fakes() -> None:
    # 표준 키 어휘가 fakes와 어긋나면 가짜/실물 테스트가 서로 다른 세계를 검증하게 됨
    assert tuple(MEDIA_INSIGHTS_METRICS) == PLATFORM_METRIC_KEYS["instagram"]


def test_unsupported_reels_metric_is_missing_not_zero() -> None:
    # 이미지 게시물: 릴스 묶음만 code=100으로 거절 → 그 키만 결측, 나머지는 그대로.
    def on_get(metrics: tuple[str, ...]) -> Any:
        if "ig_reels_avg_watch_time" in metrics:
            return _graph_error(code=100, subcode=2108006)
        return _all_present(metrics)

    poller, _ = _poller(on_get)
    by_key = {v.metric_key: v for v in poller("instagram", "image-post", 1)}

    assert by_key["avg_watch_time_ms"].missing
    assert by_key["avg_watch_time_ms"].value is None  # 0으로 채우지 않는다 (NFR-3)
    assert by_key["reach"].value == 1200.0


def test_unsupported_metric_in_universal_batch_falls_back_per_key() -> None:
    # 지표 개편으로 공용 묶음이 통째로 거절되면, 키 하나씩 물어 범인만 결측 처리한다.
    def on_get(metrics: tuple[str, ...]) -> Any:
        if "views" in metrics and len(metrics) > 1:
            return _graph_error(code=100)
        if metrics == ("views",):
            return _graph_error(code=100)
        return _all_present(metrics)

    poller, fake = _poller(on_get)
    by_key = {v.metric_key: v for v in poller("instagram", "media-1", 0)}

    assert by_key["views"].missing
    assert by_key["reach"].value == 1200.0
    assert by_key["likes"].value == 90.0
    # 공용 배치 1 + 키별 재조회 6 + 릴스 배치 1
    assert len(fake.gets) == 1 + len(_UNIVERSAL) + 1


def test_absent_entry_is_missing() -> None:
    poller, _ = _poller(lambda metrics: _payload({"reach": 1200}))
    by_key = {v.metric_key: v for v in poller("instagram", "media-1", 0)}

    assert by_key["reach"].value == 1200.0
    assert all(by_key[k].missing for k in MEDIA_INSIGHTS_METRICS if k != "reach")


def test_null_value_is_missing() -> None:
    poller, _ = _poller(lambda metrics: _payload({"reach": 1200, "likes": None}))
    by_key = {v.metric_key: v for v in poller("instagram", "media-1", 0)}

    assert by_key["likes"].missing and by_key["likes"].value is None
    assert by_key["reach"].value == 1200.0


def test_total_value_shape_is_read() -> None:
    # 일부 지표는 values 대신 total_value로 온다 — 두 모양 모두 같은 숫자로 읽는다.
    def on_get(metrics: tuple[str, ...]) -> dict[str, Any]:
        return {"data": [{"name": name, "total_value": {"value": 7}} for name in metrics]}

    poller, _ = _poller(on_get)
    by_key = {v.metric_key: v for v in poller("instagram", "media-1", 0)}

    assert by_key["shares"].value == 7.0
    assert not by_key["shares"].missing


def test_empty_data_is_all_missing() -> None:
    poller, _ = _poller(lambda metrics: {"data": []})
    values = poller("instagram", "fresh-post", 0)

    assert len(values) == len(MEDIA_INSIGHTS_METRICS)
    assert all(v.missing and v.value is None for v in values)


@pytest.mark.parametrize(
    "error",
    [
        _graph_error(code=190),  # 토큰 만료 — 결측으로 뭉개면 계정 전체가 조용히 빈다
        _graph_error(code=4),  # 쿼터
        _graph_error(code=100, subcode=33, status=400),  # 대상 미디어 없음(오타난 id)
        _graph_error(code=None, status=500),  # 서버 오류
        _graph_error(code=100, status=503),  # 5xx는 지표 탓이 아니다
        _graph_error(code=None, status=0),  # 네트워크
    ],
)
def test_api_errors_propagate(error: GraphError) -> None:
    poller, _ = _poller(lambda metrics: error)
    with pytest.raises(GraphError):
        poller("instagram", "media-1", 0)


def test_non_numeric_value_is_loud() -> None:
    # 스키마 churn을 조용한 결측으로 흡수하면 아무도 개편을 모른다.
    poller, _ = _poller(lambda metrics: _payload({"reach": "많음"}))
    with pytest.raises(ValueError):
        poller("instagram", "media-1", 0)


def test_wrong_platform_raises() -> None:
    poller, _ = _poller(_all_present)
    with pytest.raises(ValueError):
        poller("youtube", "x", 0)
