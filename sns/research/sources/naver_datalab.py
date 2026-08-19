"""네이버 데이터랩 검색어 트렌드 — 30일 검색량 추이 → 상승/유지/하락 (§2 소스 #3).

키워드 그룹별 30일 비율 시계열을 받아 첫/끝 값으로 추세를 판정한다. 산출은
"키워드: 상승" 형태의 소재 힌트(Topic Agent가 상승 키워드를 우선 고려하도록).
POST + 인증. 자격증명은 `default_service`가 바인딩(부재 시 미등록→격리).
"""

import json
import urllib.request
from datetime import date, timedelta

from sns.research.sources._http import DEFAULT_OPENER, MAX_RESPONSE_BYTES, Opener

NAVER_DATALAB_URL = "https://openapi.naver.com/v1/datalab/search"

# 추세 판정 임계 — |변화율| 10% 미만은 '유지'.
_TREND_THRESHOLD = 0.10


def _judge_trend(first: float, last: float) -> str:
    if first <= 0:
        return "상승" if last > 0 else "유지"
    change = (last - first) / first
    if change >= _TREND_THRESHOLD:
        return "상승"
    if change <= -_TREND_THRESHOLD:
        return "하락"
    return "유지"


def parse_naver_datalab(data: bytes) -> tuple[str, ...]:
    """결과 그룹별 첫/끝 비율로 추세를 판정. 점 2개 미만 그룹은 판정 불가로 제외."""
    payload = json.loads(data)
    out: list[str] = []
    for group in payload.get("results", []):
        points = group.get("data", [])
        if len(points) >= 2:
            trend = _judge_trend(float(points[0]["ratio"]), float(points[-1]["ratio"]))
            out.append(f"{group['title']}: {trend}")
    return tuple(out)


def _request_body(keywords: tuple[str, ...], *, today: date) -> bytes:
    start = today - timedelta(days=30)
    return json.dumps(
        {
            "startDate": start.isoformat(),
            "endDate": today.isoformat(),
            "timeUnit": "date",
            "keywordGroups": [{"groupName": kw, "keywords": [kw]} for kw in keywords],
        }
    ).encode("utf-8")


def fetch_naver_datalab(
    limit: int,
    *,
    client_id: str,
    client_secret: str,
    keywords: tuple[str, ...] = ("개발자", "파이썬", "AI"),
    url: str = NAVER_DATALAB_URL,
    timeout_s: float = 10.0,
    opener: Opener = DEFAULT_OPENER,
    today: date | None = None,
) -> tuple[str, ...]:
    body = _request_body(keywords, today=today or date.today())
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
            "Content-Type": "application/json",
        },
    )
    with opener(request, timeout=timeout_s) as resp:
        data = resp.read(MAX_RESPONSE_BYTES)
    return parse_naver_datalab(data)[:limit]
