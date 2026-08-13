"""`PollMetrics` 계약의 유튜브 구현 — Analytics API v2 폴링 (FR-L1, YT-3).

결측 원칙(NFR-3): API가 값을 안 주면(신선 영상 24-48h 지연·비공개 등) 해당 키는
missing=True — 절대 0으로 채우지 않는다. API '오류'는 raise로 전파한다:
PollMetrics 계약에 오류 채널이 없고, 오류를 missing으로 뭉개면 "성공했으나
값 없음"이라는 NULL의 의미가 오염되기 때문. 재시도는 호출자(러너) 몫.

window_index(0=6h,1=24h,2=72h,3+=일간)는 조회 날짜 범위에 반영하지 않는다 —
Analytics는 일 단위 granularity + 24-48h 지연이라 6h 창을 표현할 수 없다.
폴링 시점의 누적치를 그 창의 관측값으로 기록한다.
# ponytail: 누적 스냅샷 — 창별 증분이 필요해지면 startDate/endDate 계산 추가.
"""

from collections.abc import Callable
from datetime import date
from typing import Any

from sns.tools.contracts import MetricValue, Platform, PollMetrics

# 표준 metric_key(11-데이터모델 §4) → Analytics API 메트릭명.
# 플랫폼 메트릭 개편(churn) 방어: 개편 시 이 dict 1지점만 수정.
ANALYTICS_METRICS: dict[str, str] = {
    "views": "views",
    "engaged_views": "engagedViews",
    "avg_view_duration_s": "averageViewDuration",
    "avg_view_pct": "averageViewPercentage",
    "likes": "likes",
    "comments": "comments",
    "shares": "shares",
    "subscribers_gained": "subscribersGained",
}
# 채널 개설 이전이어도 무해한 고정 시작일 (누적 스냅샷 조회용).
_START_DATE = "2020-01-01"


class YouTubeMetrics:
    """Analytics 클라이언트를 `PollMetrics` 계약에 바인딩."""

    def __init__(self, analytics: Any, *, today: Callable[[], date] = date.today) -> None:
        self._analytics = analytics
        self._today = today

    def __call__(
        self, platform: Platform, post_id: str, window_index: int
    ) -> tuple[MetricValue, ...]:
        if platform != "youtube":
            raise ValueError(f"유튜브 폴러가 처리할 수 없는 platform: {platform}")
        keys = tuple(ANALYTICS_METRICS)
        response = (
            self._analytics.reports()
            .query(
                ids="channel==MINE",
                startDate=_START_DATE,
                endDate=self._today().isoformat(),
                metrics=",".join(ANALYTICS_METRICS[k] for k in keys),
                filters=f"video=={post_id}",
            )
            .execute()
        )
        rows = response.get("rows") or []
        if not rows:
            # 데이터 지연·비공개 → 정직 결측 (정상 경로)
            return tuple(MetricValue(metric_key=k, value=None, missing=True) for k in keys)
        cells = rows[0]
        values: list[MetricValue] = []
        for i, key in enumerate(keys):
            cell = cells[i] if i < len(cells) else None
            if cell is None:
                values.append(MetricValue(metric_key=key, value=None, missing=True))
            else:
                values.append(MetricValue(metric_key=key, value=float(cell), missing=False))
        return tuple(values)


# 계약 적합성을 mypy가 강제 (fakes.py의 _check_* 패턴과 동일).
_check_poll: PollMetrics = YouTubeMetrics(analytics=None)
