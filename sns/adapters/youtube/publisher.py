"""`Publish` 계약의 유튜브 구현 — 쇼츠 resumable 업로드 (FR-P2, YT-2).

C5 상태머신(`run_publish`)에 주입되는 콜러블. 업로드 1회 = 쿼터 1,600 units
(일 10,000 → 하루 약 6회, 13-로드맵 §5). 오류는 계약의 `ErrorClass`로 분류해
상태머신이 재시도 여부를 결정하게 한다(transient만 재시도).

동결 계약의 caption은 단일 문자열이라 1행=제목, 나머지=설명으로 패킹한다 —
title/description/tags 구조화는 계약 개정(PR+리뷰) 안건으로 후속.
"""

import io
from collections.abc import Callable
from typing import Any

from sns.tools.contracts import (
    ErrorClass,
    MediaAsset,
    Platform,
    Publish,
    PublishResult,
    ToolError,
)

MAX_TITLE_LEN = 100
_SHORTS_TAG = "#Shorts"
# 403 중 쿼터·한도 계열 reason — 이 외의 403은 권한 문제(auth)로 본다.
_QUOTA_REASONS = frozenset(
    {
        "quotaExceeded",
        "rateLimitExceeded",
        "userRateLimitExceeded",
        "dailyLimitExceeded",
        "uploadLimitExceeded",
    }
)


def _strip_angle_brackets(text: str) -> str:
    """'<' '>' 제거 — 유튜브가 400 invalidDescription으로 거부하는 문자다.

    실 업로드에서 드러났다: HTML 신기능 영상의 캡션에 `<dialog>`가 들어 있어 막혔다.
    웹 주제를 다루면 필연적으로 나오므로 에이전트에게 맡길 게 아니라 어댑터가 지운다.
    괄호만 빼고 이름은 남긴다 — "dialog 태그"로도 뜻이 통한다.
    """
    return text.replace("<", "").replace(">", "")


def split_caption(caption: str) -> tuple[str, str]:
    """caption 패킹 규칙: 1행 → 제목(#Shorts 보장, ≤100자), 나머지 → 설명."""
    first, _, rest = _strip_angle_brackets(caption).partition("\n")
    title = first.strip() or "Untitled"
    if _SHORTS_TAG.lower() not in title.lower():
        budget = MAX_TITLE_LEN - len(_SHORTS_TAG) - 1
        title = f"{title[:budget].rstrip()} {_SHORTS_TAG}"
    return title[:MAX_TITLE_LEN], rest.strip()


def classify_http_error(status: int, reason: str) -> ErrorClass:
    """HTTP 상태·reason → 계약 ErrorClass. 상태머신의 재시도 판단 근거."""
    if status == 401:
        return "auth"
    if status == 403:
        return "quota" if reason in _QUOTA_REASONS else "auth"
    if status >= 500:
        return "transient"
    return "permanent_unknown"


def _classify_exception(exc: Exception) -> ToolError:
    from googleapiclient.errors import HttpError

    if isinstance(exc, HttpError):
        status = exc.resp.status if exc.resp is not None else 0
        reason = ""
        details = getattr(exc, "error_details", None)
        if details and isinstance(details, list) and isinstance(details[0], dict):
            reason = str(details[0].get("reason", ""))
        return ToolError(error_class=classify_http_error(status, reason), error_raw=str(exc))
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return ToolError(error_class="transient", error_raw=str(exc))
    return ToolError(error_class="permanent_unknown", error_raw=str(exc))


class YouTubePublish:
    """유튜브 쇼츠 업로드를 `Publish` 계약에 바인딩.

    media_bytes: storage_url → mp4 바이트 (MediaStore 조회 seam).
    첫 실전은 privacy_status="private" — 미감사 API 프로젝트는 어차피 private 잠금.
    """

    def __init__(
        self,
        youtube: Any,
        *,
        media_bytes: Callable[[str], bytes],
        privacy_status: str = "private",
        category_id: str = "22",  # People & Blogs
    ) -> None:
        self._youtube = youtube
        self._media_bytes = media_bytes
        self._privacy_status = privacy_status
        self._category_id = category_id

    def __call__(
        self,
        platform: Platform,
        media: MediaAsset,
        caption: str,
        idempotency_key: str,
        container_id: str | None = None,
    ) -> PublishResult:
        if platform != "youtube":
            raise ValueError(f"유튜브 어댑터가 처리할 수 없는 platform: {platform}")
        if media.kind != "video":
            raise ValueError(f"쇼츠 업로드는 video만 가능: {media.kind}")
        # ponytail: resumable URI의 크로스 프로세스 복구(container_id) 미구현 —
        # 상태머신의 종결상태 무재호출 + transient 재시도로 충분. 필요해지면 URI 영속.
        from googleapiclient.http import MediaIoBaseUpload

        title, description = split_caption(caption)
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": self._category_id,
            },
            "status": {
                "privacyStatus": self._privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }
        try:
            data = self._media_bytes(media.storage_url)
            upload = MediaIoBaseUpload(
                io.BytesIO(data), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True
            )
            request = self._youtube.videos().insert(
                part="snippet,status", body=body, media_body=upload
            )
            response = None
            while response is None:
                _, response = request.next_chunk()
            return PublishResult(post_id=str(response["id"]))
        except Exception as exc:  # 경계에서 ErrorClass로 번역하는 것이 이 어댑터의 책무
            return PublishResult(error=_classify_exception(exc))


# 계약 적합성을 mypy가 강제 (fakes.py의 _check_* 패턴과 동일).
_check_publish: Publish = YouTubePublish(None, media_bytes=lambda _: b"")
