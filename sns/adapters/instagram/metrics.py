"""`PollMetrics` 계약의 인스타그램 구현 — Graph media insights 폴링 (FR-L1, IG-3).

발행 원장의 `external_post_id`(=IG 미디어 ID)로 `/{media-id}/insights`를 부르고,
표준 metric_key(11-데이터모델 §4)로 옮겨 `MetricValue` 튜플을 돌려준다. **DB를 모른다**
— 적재는 러너(`sns/learning/poller.py`) 몫이다.

결측 원칙(NFR-3): API가 값을 안 주면 그 키는 missing=True — 절대 0으로 채우지 않는다.
API '오류'는 raise로 전파한다(YT 폴러가 같은 결정을 적어 뒀다): `PollMetrics` 계약에
오류 채널이 없고, 오류를 결측으로 뭉개면 "성공했으나 값 없음"이라는 NULL의 의미가
오염된다. 재시도·격리는 호출자(러너) 몫이다.

**요청을 두 번 나눠 보낸다.** IG는 미디어 종류에 따라 지원 지표가 다르고, 한 배치에
지원되지 않는 지표가 **하나라도** 섞이면 code=100으로 **배치 전체가 죽는다**. 릴스 전용
지표를 같이 보내면 이미지 게시물의 reach·likes까지 한꺼번에 잃는다. 그래서 공용 묶음과
릴스 전용 묶음을 갈라 부른다(정상 경로 2콜) — 릴스가 아니면 릴스 묶음만 결측이 된다.

그래도 지원 여부가 어긋나면(플랫폼 지표 개편) 그 묶음만 **키 하나씩** 다시 부른다:
Graph는 "어느 지표가 문제인지"를 기계가 읽을 수 있게 알려주지 않으므로, 메시지를
파싱해 추측하는 대신 한 번 더 물어 사실로 가른다. 비정상 경로에서만 콜이 늘어난다.

window_index(0=6h·1=24h·2=72h·3+=일간)는 요청에 반영하지 않는다 — media insights는
lifetime 누적이라 창별 증분을 표현할 수 없다. 폴링 시점의 누적치를 그 창의 관측값으로
기록한다(YT 폴러와 같은 규약).
# ponytail: 창별 증분이 필요해지면 직전 창 관측과의 차분은 러너/리포트 층에서 낸다.

스레드 안전: `UrllibGraphHttp`는 호출마다 새 커넥션을 열어 상태를 공유하지 않는다 —
YT 폴러의 `threading.Lock`(httplib2 keep-alive 공유)이 여기엔 필요 없다.
"""

from typing import Any

from sns.adapters.instagram.publisher import GraphError, GraphHttp, UrllibGraphHttp
from sns.tools.contracts import MetricValue, Platform, PollMetrics

# media insights의 `views`는 v22부터다(11-데이터모델 §4 — plays·impressions는 은퇴).
# 발행(publisher)은 계속 제 버전을 쓴다: 발행 경로를 지표 때문에 흔들지 않는다.
INSIGHTS_API_VERSION = "v22.0"

# 표준 metric_key(11-데이터모델 §4) → Graph insights 메트릭명.
# 플랫폼 메트릭 개편(churn) 방어: 개편 시 이 dict 1지점만 수정.
# 순서는 `sns/tools/fakes.PLATFORM_METRIC_KEYS["instagram"]`과 같아야 한다(테스트가 강제).
MEDIA_INSIGHTS_METRICS: dict[str, str] = {
    "reach": "reach",
    "likes": "likes",
    "shares": "shares",
    "saved": "saved",
    "comments": "comments",
    "avg_watch_time_ms": "ig_reels_avg_watch_time",
    "views": "views",
}

# 릴스에만 있는 지표 — 나머지와 한 배치에 섞지 않는다(위 docstring).
# skip_rate(`reels_skip_rate`)가 일반 공개되면 여기에 들어온다.
REELS_ONLY_KEYS: frozenset[str] = frozenset({"avg_watch_time_ms"})

# insights GET에서 우리가 바꾸는 파라미터는 `metric` 하나뿐이라, code=100(invalid
# parameter)은 "이 미디어에 그 지표가 없다"로 읽는다. 단 subcode=33은 대상 자체가
# 없다는 뜻이라 결측이 아니다 — 오타난 post_id가 조용히 NULL로 착지하면 안 된다.
_INVALID_PARAM_CODE = 100
_OBJECT_MISSING_SUBCODE = 33

# 공용 묶음 먼저, 릴스 전용 묶음 나중 — dict 순서(=표준 키 순서)를 그대로 보존한다.
_METRIC_GROUPS: tuple[tuple[str, ...], ...] = (
    tuple(k for k in MEDIA_INSIGHTS_METRICS if k not in REELS_ONLY_KEYS),
    tuple(k for k in MEDIA_INSIGHTS_METRICS if k in REELS_ONLY_KEYS),
)


def _is_unsupported_metric(exc: GraphError) -> bool:
    """이 미디어가 요청 지표를 지원하지 않는가 — 결측으로 착지시킬 유일한 오류."""
    if exc.code != _INVALID_PARAM_CODE or exc.subcode == _OBJECT_MISSING_SUBCODE:
        return False
    # 5xx는 서버 사정(transient) — 지표 탓으로 돌리면 진짜 결측과 구분이 사라진다.
    return exc.http_status < 500


def _cell(entry: dict[str, Any]) -> float | None:
    """insights 항목 1개 → 숫자. lifetime(`values`)·집계(`total_value`) 두 모양 모두.

    숫자가 아닌 값은 뭉개지 않고 그대로 터뜨린다 — 스키마 churn은 조용한 결측보다
    시끄러운 실패가 낫다(YT 폴러의 `float(cell)`과 같은 태도).
    """
    values = entry.get("values")
    if isinstance(values, list) and values:
        first = values[0]
        raw = first.get("value") if isinstance(first, dict) else None
    else:
        total = entry.get("total_value")
        raw = total.get("value") if isinstance(total, dict) else None
    return None if raw is None else float(raw)


class InstagramMetrics:
    """Graph media insights를 `PollMetrics` 계약에 바인딩.

    http: 발행 어댑터와 같은 `GraphHttp` seam — 테스트는 가짜를 주입해 네트워크 없이 돈다.
    access_token: IG 비즈니스 계정 토큰(insights 권한). 평문 보관 금지는 호출자 책임이다
    (NFR-7 — `channel.token_encrypted`).
    """

    def __init__(self, http: GraphHttp, *, access_token: str) -> None:
        self._http = http
        self._access_token = access_token

    def __call__(
        self, platform: Platform, post_id: str, window_index: int
    ) -> tuple[MetricValue, ...]:
        if platform != "instagram":
            raise ValueError(f"인스타그램 폴러가 처리할 수 없는 platform: {platform}")
        collected: dict[str, float | None] = {}
        for group in _METRIC_GROUPS:
            if group:
                collected.update(self._fetch(post_id, group))
        # 표준 키 전량을 항상 같은 순서로 돌려준다 — 응답에 없던 키는 정직 결측.
        return tuple(
            MetricValue(
                metric_key=key, value=collected.get(key), missing=collected.get(key) is None
            )
            for key in MEDIA_INSIGHTS_METRICS
        )

    def _fetch(self, post_id: str, keys: tuple[str, ...]) -> dict[str, float | None]:
        try:
            data = self._http.get(
                f"/{post_id}/insights",
                {
                    "metric": ",".join(MEDIA_INSIGHTS_METRICS[k] for k in keys),
                    "access_token": self._access_token,
                },
            )
        except GraphError as exc:
            if not _is_unsupported_metric(exc):
                raise  # 인증·쿼터·5xx·네트워크 → 러너가 그 건만 격리하고 원장에 남긴다
            if len(keys) == 1:
                return {keys[0]: None}  # 이 미디어에 없는 지표 = 정직 결측
            merged: dict[str, float | None] = {}
            for key in keys:  # 어느 키가 문제인지 API가 알려주지 않는다 → 하나씩 확인
                merged.update(self._fetch(post_id, (key,)))
            return merged
        return _read_values(data, keys)


def _read_values(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, float | None]:
    entries = data.get("data")
    by_name: dict[Any, dict[str, Any]] = {
        entry.get("name"): entry
        for entry in (entries if isinstance(entries, list) else [])
        if isinstance(entry, dict)
    }
    out: dict[str, float | None] = {}
    for key in keys:
        entry = by_name.get(MEDIA_INSIGHTS_METRICS[key])
        out[key] = None if entry is None else _cell(entry)
    return out


# 계약 적합성을 mypy가 강제 (fakes.py의 _check_* 패턴과 동일).
_check_poll: PollMetrics = InstagramMetrics(
    UrllibGraphHttp(version=INSIGHTS_API_VERSION), access_token="x"
)
