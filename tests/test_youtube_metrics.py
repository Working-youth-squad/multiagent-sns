"""유튜브 Analytics 폴러 — 키 매핑·정직 결측. 네트워크 0."""

from datetime import date
from typing import Any

import pytest

from sns.adapters.youtube.metrics import ANALYTICS_METRICS, YouTubeMetrics
from sns.tools.fakes import PLATFORM_METRIC_KEYS


class _FakeAnalytics:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.queries: list[dict[str, Any]] = []

    def reports(self) -> "_FakeAnalytics":
        return self

    def query(self, **kwargs: Any) -> "_FakeAnalytics":
        self.queries.append(kwargs)
        return self

    def execute(self) -> dict[str, Any]:
        return self._response


def _poller(response: dict[str, Any]) -> tuple[YouTubeMetrics, _FakeAnalytics]:
    fake = _FakeAnalytics(response)
    return YouTubeMetrics(fake, today=lambda: date(2026, 8, 13)), fake


def test_maps_row_to_standard_keys() -> None:
    keys = tuple(ANALYTICS_METRICS)
    row = [1000, 800, 12.5, 41.7, 50, 3, 7, 2]
    poller, fake = _poller({"rows": [row]})
    values = poller("youtube", "XoB6SuTMEvQ", 0)
    by_key = {v.metric_key: v for v in values}
    assert by_key["views"].value == 1000.0
    assert by_key["engaged_views"].value == 800.0
    assert by_key["avg_view_pct"].value == 41.7
    assert all(not by_key[k].missing for k in keys)
    query = fake.queries[0]
    assert query["filters"] == "video==XoB6SuTMEvQ"
    assert query["endDate"] == "2026-08-13"


def test_empty_rows_is_honest_missing() -> None:
    poller, _ = _poller({"rows": []})
    values = poller("youtube", "fresh-video", 0)
    assert len(values) == len(ANALYTICS_METRICS)
    assert all(v.missing and v.value is None for v in values)


def test_none_cell_is_missing() -> None:
    row = [1000, None, 12.5, 41.7, 50, 3, 7, 2]
    poller, _ = _poller({"rows": [row]})
    by_key = {v.metric_key: v for v in poller("youtube", "x", 0)}
    assert by_key["engaged_views"].missing
    assert by_key["views"].value == 1000.0


def test_wrong_platform_raises() -> None:
    poller, _ = _poller({"rows": []})
    with pytest.raises(ValueError):
        poller("instagram", "x", 0)


def test_metric_keys_consistent_with_fakes() -> None:
    # 표준 키 어휘가 fakes와 어긋나면 가짜/실물 테스트가 서로 다른 세계를 검증하게 됨
    assert tuple(ANALYTICS_METRICS) == PLATFORM_METRIC_KEYS["youtube"]
