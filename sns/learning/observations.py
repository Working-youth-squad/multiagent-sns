"""저장된 관측을 읽는 자리 (M6 공용) — reward(FR-L2)와 분석글(FR-L5)이 함께 쓴다.

## 왜 따로 있나

`sns/agents/analyst.py`의 `run_analysis(...)`는 **`PollMetrics`를 인자로 받아 직접
호출한다**. 실 어댑터를 그대로 물리면 이미 DB에 있는 값을 두고 API를 다시 때린다 —
쿼터를 태우고, 폴링 시점과 분석 시점의 값이 달라 "관측창"이라는 말이 무의미해진다
(같은 창을 두 번 읽었는데 숫자가 다르면 그건 창이 아니다).

그렇다고 보상 쪽과 분석 쪽이 각자 "관측 → 지표" 변환을 만들면 두 벌이 생기고, 한쪽만
고쳐지는 날이 온다 — `run_profile_cycle.py`에 배선 블록이 두 벌이던 사고가 그 모양이었다.
그래서 **읽는 조각은 여기 하나**다.

## 없는 것은 조용히 결측으로 만들지 않는다

`StoredMetrics`는 모르는 게시물·안 찍힌 창에서 **예외를 던진다**. 빈 튜플이나 전 결측을
돌려주면 "폴링을 아직 안 했다"가 "API가 값을 안 줬다"로 둔갑하고, 그 둘은 스코어보드에서
다른 판정을 받는다(NFR-3). 호출부는 `available_posts()`로 **먼저 걸러서** 부른다.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime

from sns.learning.stores import MetricStore
from sns.tools.contracts import MetricValue, Platform, PollMetrics


def as_metric_map(values: Sequence[MetricValue]) -> dict[str, float | None]:
    """관측값 → `{metric_key: 값 또는 None}` — 스코어보드·산식의 입력 모양.

    결측은 키를 빼지 않고 **None으로 남긴다**. 키가 없는 것과 값이 없는 것은 다르다:
    앞은 "그 지표를 안 본다", 뒤는 "봤는데 플랫폼이 안 줬다".
    """
    return {v.metric_key: v.value for v in values}


class UnknownObservation(LookupError):
    """저장소에 없는 게시물·창 — 폴링 전이거나 다른 채널의 post_id."""


class StoredMetrics:
    """저장된 관측을 `PollMetrics` 계약으로 되먹인다. 네트워크를 타지 않는다.

    발행 원장을 한 번 훑어 `(platform, post_id) → publication_id` 색인을 만든다.
    `PollMetrics`가 post_id로 말하고 저장소는 publication_id로 말하기 때문이다.
    색인은 생성 시점에 고정된다 — 분석 도중 원장이 자라도 같은 표본을 본다(결정론).
    """

    def __init__(
        self, store: MetricStore, *, since: datetime | None = None, limit: int = 500
    ) -> None:
        self._store = store
        items = store.published_items(since=since, limit=limit)
        self._index: dict[tuple[Platform, str], str] = {
            (item.platform, item.external_post_id): item.publication_id for item in items
        }
        self._windows: dict[tuple[Platform, str], tuple[int, ...]] = {
            (item.platform, item.external_post_id): item.observed_windows for item in items
        }

    def available_posts(self, platform: Platform, *, window_index: int) -> tuple[str, ...]:
        """그 창이 **이미 찍힌** 게시물만. 분석·보상의 표본은 여기서 고른다."""
        return tuple(
            post_id
            for (plat, post_id), windows in self._windows.items()
            if plat == platform and window_index in windows
        )

    def publication_id(self, platform: Platform, post_id: str) -> str:
        try:
            return self._index[(platform, post_id)]
        except KeyError as exc:
            raise UnknownObservation(f"발행 원장에 없는 게시물: {platform}/{post_id}") from exc

    def __call__(
        self, platform: Platform, post_id: str, window_index: int
    ) -> tuple[MetricValue, ...]:
        values = self._store.read_observation(
            publication_id=self.publication_id(platform, post_id), window_index=window_index
        )
        if not values:
            raise UnknownObservation(
                f"아직 찍히지 않은 창: {platform}/{post_id} window={window_index} "
                "(available_posts로 먼저 거를 것)"
            )
        return values

    def metrics_of(
        self, platform: Platform, post_id: str, window_index: int
    ) -> Mapping[str, float | None]:
        """`__call__` + `as_metric_map` — 산식 쪽이 두 줄 쓰지 않게."""
        return as_metric_map(self(platform, post_id, window_index))


# mypy(sns): 저장소 되먹이기가 동결된 PollMetrics 계약을 그대로 만족함을 강제 —
# analyst는 이것이 실 어댑터인지 저장소인지 알 필요가 없다.
def _check(stored: StoredMetrics) -> PollMetrics:
    return stored
