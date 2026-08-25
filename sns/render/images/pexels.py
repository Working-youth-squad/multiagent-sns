"""Pexels 검색·다운로드 어댑터 — `StockImage`로 정규화한다.

Pexels License는 자산마다 다르지 않고 **하나**다(상업 이용 허용, 크레딧 불필요). 그래서
게이트가 볼 라이선스 판정은 "이게 정말 Pexels에서 왔는가"로 좁혀진다 — CC처럼 자산별
라이선스를 해석할 필요가 없는 대신, **출처를 신뢰할 수 있어야** 한다. 다운로드 호스트를
`images.pexels.com`으로 못박는 이유가 그것이다(SSRF 통로 겸 라이선스 근거).

무료 키 한도: 200 req/시, 20,000 req/월. 이미지 해소는 컷당 1회라 여유가 크다.
"""

import json
import os
import urllib.parse
import urllib.request
from urllib.parse import urlparse

from sns.net.http import DEFAULT_OPENER, Opener, fetch_bytes
from sns.render.images.gate import StockImage, screen_query

ENV_PEXELS_API_KEY = "PEXELS_API_KEY"
SEARCH_URL = "https://api.pexels.com/v1/search"
# 다운로드를 허용하는 호스트. 라이선스 근거이자 SSRF 방어선이다.
ALLOWED_IMAGE_HOST = "images.pexels.com"
# 사진 1장 바이트 상한. large2x는 보통 1~2MB라 넉넉하다.
MAX_IMAGE_BYTES = 12_000_000
DEFAULT_LIMIT = 15
TIMEOUT_S = 15.0
# Cloudflare가 urllib 기본 UA("Python-urllib/3.x")를 error 1010으로 막는다. 키가 맞아도
# 403이 떨어져 "키가 잘못됐나"로 오해하기 딱 좋은 실패라, UA를 명시해 못박는다.
USER_AGENT = "multiagent-sns/0.1 (+https://github.com/Working-youth-squad/multiagent-sns)"
# 940 정사각에 쓸 거라 original(10MB+)까지 갈 이유가 없다. 앞에서부터 있는 것을 쓴다.
_SRC_PREFERENCE = ("large2x", "large", "original", "medium")


class PexelsError(RuntimeError):
    """Pexels 호출·응답 처리 실패 — 설정 누락·금지 질의 포함."""


def _photo_to_stock(photo: object) -> StockImage | None:
    """Pexels 사진 1건 → StockImage. 쓸 수 있는 src가 없으면 None(건너뛴다)."""
    if not isinstance(photo, dict):
        raise PexelsError(f"응답의 photos 항목이 객체가 아님: {photo!r}")
    src = photo.get("src")
    if not isinstance(src, dict):
        return None
    download_url = next((str(src[k]) for k in _SRC_PREFERENCE if src.get(k)), "")
    if not download_url:
        return None
    try:
        return StockImage(
            source="pexels",
            source_id=str(photo["id"]),
            page_url=str(photo.get("url", "")),
            download_url=download_url,
            width=int(photo["width"]),
            height=int(photo["height"]),
            alt=str(photo.get("alt") or ""),
            photographer=str(photo.get("photographer", "")),
            license_id="pexels",
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PexelsError(f"응답 사진 항목이 예상 모양과 다름: {exc}") from exc


def parse_search_response(payload: bytes) -> list[StockImage]:
    """검색 응답 바이트 → StockImage 목록. 네트워크와 분리된 순수 함수."""
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise PexelsError(f"응답 JSON 파싱 실패: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("photos"), list):
        raise PexelsError(f"응답에 photos 리스트가 없음: {str(data)[:120]}")
    parsed = (_photo_to_stock(p) for p in data["photos"])
    return [img for img in parsed if img is not None]


def search_pexels(
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    opener: Opener = DEFAULT_OPENER,
) -> list[StockImage]:
    """검색어 → 후보 목록. 게이트를 **요청 전에** 통과해야 한다(FR-Q7)."""
    verdict = screen_query(query)
    if not verdict.allowed:
        raise PexelsError(f"검색어가 게이트에 막힘 — {verdict.reason}")
    api_key = os.environ.get(ENV_PEXELS_API_KEY)
    if not api_key:
        raise PexelsError(
            f"env {ENV_PEXELS_API_KEY}가 없습니다 — https://www.pexels.com/api/ 에서 "
            "무료 키를 발급받아 .env에 넣으세요"
        )
    # orientation은 걸지 않는다. square로 좁혔더니 결과가 8,000건에서 616건으로 줄고
    # 상위가 전부 무관한 사진이 됐다("server room racks" → 케이크 상자). 비율은 검색으로
    # 좁히는 게 아니라 크롭으로 해결한다.
    params = urllib.parse.urlencode({"query": query, "per_page": limit})
    request = urllib.request.Request(
        f"{SEARCH_URL}?{params}",
        headers={"Authorization": api_key, "User-Agent": USER_AGENT},
    )
    try:
        payload = fetch_bytes(request, timeout_s=TIMEOUT_S, opener=opener)
    except OSError as exc:
        raise PexelsError(f"Pexels 검색 실패: {exc}") from exc
    return parse_search_response(payload)


def download_image(url: str, *, opener: Opener = DEFAULT_OPENER) -> bytes:
    """사진 바이트 다운로드. 호스트를 못박아 임의 URL 호출로 번지지 않게 한다."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise PexelsError(f"https만 허용: {url!r}")
    if parsed.hostname != ALLOWED_IMAGE_HOST:
        raise PexelsError(f"허용되지 않은 호스트({ALLOWED_IMAGE_HOST}만 가능): {parsed.hostname!r}")
    try:
        # 상한 +1까지 읽어, 상한에 딱 걸린 것과 잘린 것을 구분한다.
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        data = fetch_bytes(
            request, timeout_s=TIMEOUT_S, opener=opener, max_bytes=MAX_IMAGE_BYTES + 1
        )
    except OSError as exc:
        raise PexelsError(f"이미지 다운로드 실패: {exc}") from exc
    if len(data) > MAX_IMAGE_BYTES:
        raise PexelsError(f"이미지 크기가 상한을 넘음 — {MAX_IMAGE_BYTES:,}바이트 이하만 허용")
    return data
