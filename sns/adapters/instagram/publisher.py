"""`Publish` 계약의 인스타그램 구현 — Graph API 2단계 발행 (FR-P1, IG-2).

C5 상태머신(`run_publish`)에 주입되는 콜러블. IG는 유튜브와 달리 발행이 2단계다:

    (container_id 없음) → 미디어 컨테이너 생성 → transient 반환(container_created 진입)
    (container_id 있음) → 상태 폴링 → FINISHED면 게시, IN_PROGRESS면 transient 재시도

이 두 단계를 한 `__call__`이 상태머신의 `container_id` 인자로 분기해 표현한다 —
state_machine.docstring이 이미 이 패턴("IG 2단계 컨테이너 ID 보존·재사용")을 전제한다.

동결 계약상 미디어는 바이트가 아니라 `storage_url`(내부 저장소 주소)이라, Graph API가
요구하는 공개 https URL로 바꾸는 `media_url_resolver` 콜러블을 주입받는다(YouTube
어댑터의 `media_bytes` seam과 동형) — 벤더(GCS 서명 URL 등) 배선은 호출자 몫.

오류 분류는 HTTP status + Graph 오류 바디의 `code`/`error_subcode`로 판정한다. 코드
매핑은 Meta 문서 공개 값 기준 최소 집합 — 실 계정으로 검증 전까지는 근사치이며,
미분류는 `error_raw` 원문을 보존해 후속 조정을 가능하게 한다(FR-P4).
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any, Protocol

from sns.tools.contracts import (
    ErrorClass,
    MediaAsset,
    Platform,
    Publish,
    PublishResult,
    ToolError,
)

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# 컨테이너 상태 폴링 결과 (Graph API status_code 필드). ERROR·EXPIRED·미확인 값은
# 모두 재시도 여지 없는 종결로 취급하므로 별도 상수 없이 _advance의 폴백으로 처리한다.
_STATUS_FINISHED = "FINISHED"
_STATUS_IN_PROGRESS = "IN_PROGRESS"

# 인증 무효/만료 (Graph 표준 code=190, OAuthException 계열)
_AUTH_CODES = frozenset({190})
# 한도·요율 계열 code (Application/Page-level rate limit)
_QUOTA_CODES = frozenset({4, 17, 32, 613})
# 스팸/정책 위반 계열 error_subcode (콘텐츠 게시 정책 차단) — 공개 문서 기준 예시값,
# 실 계정 관측치가 쌓이면 갱신한다.
_SPAM_SUBCODES = frozenset({2207003, 2207032, 1349048, 1349172})


class GraphError(RuntimeError):
    """Graph API가 비-2xx 또는 오류 바디를 돌려줌 — 어댑터 경계에서 ErrorClass로 번역."""

    def __init__(self, *, http_status: int, code: int | None, subcode: int | None, raw: str):
        super().__init__(raw)
        self.http_status = http_status
        self.code = code
        self.subcode = subcode
        self.raw = raw


class _Response(Protocol):
    status: int

    def read(self, amt: int = ..., /) -> bytes: ...


Opener = Callable[..., AbstractContextManager[_Response]]


class GraphHttp(Protocol):
    """Graph API 호출 seam — 테스트는 가짜를 주입해 네트워크 없이 돈다."""

    def post(self, path: str, params: Mapping[str, str]) -> dict[str, Any]: ...
    def get(self, path: str, params: Mapping[str, str]) -> dict[str, Any]: ...


class UrllibGraphHttp:
    """`GraphHttp`의 운영 구현 — urllib 기반, opener 주입점 보존.

    `version`은 호출부가 필요할 때만 올린다(기본은 발행이 검증한 버전 그대로). 지표
    폴러가 v22를 요구하는데(media insights의 `views`) 발행 버전을 같이 끌어올리면,
    지표 때문에 발행 경로가 바뀐다 — 그래서 상수가 아니라 인자다.
    """

    def __init__(
        self,
        *,
        timeout_s: float = 15.0,
        opener: Opener = urllib.request.urlopen,
        version: str = GRAPH_API_VERSION,
    ) -> None:
        self._timeout_s = timeout_s
        self._opener = opener
        self._base = f"https://graph.facebook.com/{version}"

    def post(self, path: str, params: Mapping[str, str]) -> dict[str, Any]:
        body = urllib.parse.urlencode(params).encode("utf-8")
        request = urllib.request.Request(f"{self._base}{path}", data=body, method="POST")
        return self._call(request)

    def get(self, path: str, params: Mapping[str, str]) -> dict[str, Any]:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(f"{self._base}{path}?{query}", method="GET")
        return self._call(request)

    def _call(self, request: urllib.request.Request) -> dict[str, Any]:
        data: dict[str, Any]
        try:
            with self._opener(request, timeout=self._timeout_s) as resp:
                status = resp.status
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                data = json.loads(exc.read())
            except (ValueError, OSError):
                data = {}
            _raise_if_error(status, data)
            return data
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GraphError(http_status=0, code=None, subcode=None, raw=str(exc)) from exc
        _raise_if_error(status, data)
        return data


def _raise_if_error(status: int, data: dict[str, Any]) -> None:
    error = data.get("error") if isinstance(data, dict) else None
    if error is None and 200 <= status < 300:
        return
    if isinstance(error, dict):
        raise GraphError(
            http_status=status,
            code=error.get("code"),
            subcode=error.get("error_subcode"),
            raw=json.dumps(error, ensure_ascii=False),
        )
    raise GraphError(http_status=status, code=None, subcode=None, raw=json.dumps(data))


def classify_graph_error(*, http_status: int, code: int | None, subcode: int | None) -> ErrorClass:
    """Graph 오류 → 계약 ErrorClass. 상태머신의 재시도 판단 근거."""
    if subcode in _SPAM_SUBCODES:
        return "spam_block"
    if code in _AUTH_CODES:
        return "auth"
    if code in _QUOTA_CODES:
        return "quota"
    if http_status == 0 or http_status >= 500:
        return "transient"
    return "permanent_unknown"


def _classify_exception(exc: Exception) -> ToolError:
    if isinstance(exc, GraphError):
        return ToolError(
            error_class=classify_graph_error(
                http_status=exc.http_status, code=exc.code, subcode=exc.subcode
            ),
            error_raw=exc.raw,
        )
    return ToolError(error_class="permanent_unknown", error_raw=str(exc))


class InstagramPublish:
    """IG Graph API 컨테이너 생성→폴링→게시를 `Publish` 계약에 바인딩.

    media_url_resolver: storage_url(내부 주소) → 공개 https URL(Graph API 필수 입력).
    caption 패킹은 계약대로 단일 문자열 그대로 전달(IG는 캡션 형식 제약이 유튜브보다
    느슨해 별도 분할이 불필요).
    """

    def __init__(
        self,
        http: GraphHttp,
        *,
        ig_user_id: str,
        access_token: str,
        media_url_resolver: Callable[[str], str],
    ) -> None:
        self._http = http
        self._ig_user_id = ig_user_id
        self._access_token = access_token
        self._resolve_url = media_url_resolver

    def __call__(
        self,
        platform: Platform,
        media: MediaAsset,
        caption: str,
        idempotency_key: str,
        container_id: str | None = None,
    ) -> PublishResult:
        if platform != "instagram":
            raise ValueError(f"인스타그램 어댑터가 처리할 수 없는 platform: {platform}")
        if media.kind not in ("image", "video"):
            raise ValueError(f"피드/릴스 발행은 image·video만 가능: {media.kind}")

        try:
            if container_id is None:
                new_id = self._create_container(media, caption)
                # 컨테이너 생성 직후엔 아직 처리 중 — transient로 반환해 상태머신이
                # container_created로 전진하며 container_id를 보존하게 한다.
                return PublishResult(
                    container_id=new_id,
                    error=ToolError(error_class="transient", error_raw="container_created"),
                )
            return self._advance(container_id)
        except Exception as exc:  # 경계에서 ErrorClass로 번역하는 것이 이 어댑터의 책무
            return PublishResult(container_id=container_id, error=_classify_exception(exc))

    def _create_container(self, media: MediaAsset, caption: str) -> str:
        url = self._resolve_url(media.storage_url)
        params = {"caption": caption, "access_token": self._access_token}
        if media.kind == "video":
            params["media_type"] = "REELS"
            params["video_url"] = url
        else:
            params["image_url"] = url
        data = self._http.post(f"/{self._ig_user_id}/media", params)
        return str(data["id"])

    def _advance(self, container_id: str) -> PublishResult:
        status_data = self._http.get(
            f"/{container_id}", {"fields": "status_code", "access_token": self._access_token}
        )
        status = status_data.get("status_code")
        if status == _STATUS_FINISHED:
            publish_data = self._http.post(
                f"/{self._ig_user_id}/media_publish",
                {"creation_id": container_id, "access_token": self._access_token},
            )
            return PublishResult(post_id=str(publish_data["id"]), container_id=container_id)
        if status == _STATUS_IN_PROGRESS:
            return PublishResult(
                container_id=container_id,
                error=ToolError(error_class="transient", error_raw=f"status={status}"),
            )
        # ERROR·EXPIRED·미확인 값 — 재시도 여지 없음(종결 failed).
        return PublishResult(
            container_id=container_id,
            error=ToolError(error_class="permanent_unknown", error_raw=f"status={status}"),
        )


# 계약 적합성을 mypy가 강제 (fakes.py의 _check_* 패턴과 동일).
_check_publish: Publish = InstagramPublish(
    UrllibGraphHttp(), ig_user_id="x", access_token="x", media_url_resolver=lambda u: u
)
