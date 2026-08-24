"""알림 도메인 (C6, FR-W4) — 알림 이벤트 모델 + `run_event` 이중적재 매핑.

이 모듈은 **순수**하다: 네트워크·LLM·DB와 무관하게 "알림이 무엇인지"만 정의한다.
전송(Discord)은 [sns.notify.discord], 원인 1줄(LLM)은 [sns.notify.cause],
적재·오케스트레이션은 [sns.notify.dispatch]에 둔다.

오류 분류는 동결 계약 `ErrorClass`(auth/quota/spam_block/transient/permanent_unknown)를
그대로 쓴다 — Publisher 실패가 만든 `ToolError`를 손실 없이 실어 나른다. 품질 게이트
차단은 시스템 오류가 아니라 콘텐츠 거부이므로 `error_class` 없이 notice로 적재한다.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from sns.tools.contracts import ErrorClass, Platform, ToolError

# run_event.kind는 'error'|'notice' 둘로만 적재한다 (001_initial.sql CHECK와 일치).
Severity = Literal["error", "info"]

AlertKind = Literal[
    "publish_success",
    "publish_failure",
    "token_expiry",
    "quota_exceeded",
    "quality_blocked",
    "cycle_error",
    "manual_assigned",
    "manual_registered",
]


@dataclass(frozen=True)
class Alert:
    """통지 1건. `cause_line`은 dispatch가 LLM 분석으로 채운다(없으면 None)."""

    kind: AlertKind
    severity: Severity
    title: str
    platform: Platform | None = None
    error_class: ErrorClass | None = None
    error_raw: str | None = None
    cause_line: str | None = None
    context: Mapping[str, str] = field(default_factory=dict)


# ── 팩토리: 발신 지점(Publisher 실패처리부·사이클 catch)의 호출을 간결하게 ──


def publish_success(platform: Platform, *, post_id: str, publication_id: str) -> Alert:
    return Alert(
        kind="publish_success",
        severity="info",
        title=f"[{platform}] 발행 성공",
        platform=platform,
        context={"publication_id": publication_id, "post_id": post_id},
    )


def publish_failure(platform: Platform, error: ToolError, *, publication_id: str) -> Alert:
    return Alert(
        kind="publish_failure",
        severity="error",
        title=f"[{platform}] 발행 실패 — {error.error_class}",
        platform=platform,
        error_class=error.error_class,
        error_raw=error.error_raw,
        context={"publication_id": publication_id},
    )


def token_expiry(platform: Platform, *, detail: str) -> Alert:
    return Alert(
        kind="token_expiry",
        severity="error",
        title=f"[{platform}] 토큰 만료/무효",
        platform=platform,
        error_class="auth",
        error_raw=detail,
    )


def quota_exceeded(platform: Platform, *, detail: str) -> Alert:
    return Alert(
        kind="quota_exceeded",
        severity="error",
        title=f"[{platform}] 한도 초과",
        platform=platform,
        error_class="quota",
        error_raw=detail,
    )


def quality_blocked(*, publication_id: str, reason: str, platform: Platform | None = None) -> Alert:
    return Alert(
        kind="quality_blocked",
        severity="info",  # 콘텐츠 거부 = notice (시스템 오류 아님)
        title="품질 게이트 차단",
        platform=platform,
        context={"publication_id": publication_id, "reason": reason},
    )


def cycle_error(*, error_raw: str, cycle_id: str | None = None) -> Alert:
    return Alert(
        kind="cycle_error",
        severity="error",
        title="사이클 실패 (최상위 catch)",
        error_raw=error_raw,
        context={"cycle_id": cycle_id} if cycle_id else {},
    )


def manual_assigned(
    platform: Platform, *, channel_id: str, topic_title: str, cycle_id: str
) -> Alert:
    """수동(manual) 채널에 주제가 배정됨 — 사람이 직접 작성·발행해야 한다(FR-E5)."""
    return Alert(
        kind="manual_assigned",
        severity="info",
        title=f"[{platform}] 수동 발행 주제 배정 — 사람 작성 필요",
        platform=platform,
        context={"channel_id": channel_id, "topic_title": topic_title, "cycle_id": cycle_id},
    )


def manual_registered(
    platform: Platform, *, channel_id: str, external_post_id: str, publication_id: str
) -> Alert:
    """사람이 직접 발행한 게시물이 [sns.publish.manual]로 등록 완료됨."""
    return Alert(
        kind="manual_registered",
        severity="info",
        title=f"[{platform}] 수동 발행 등록 완료",
        platform=platform,
        context={
            "channel_id": channel_id,
            "external_post_id": external_post_id,
            "publication_id": publication_id,
        },
    )


# ── run_event 이중적재 매핑 (FR-W4: 모든 알림은 DB에도 적재) ──


def event_kind(alert: Alert) -> str:
    """알림 severity → run_event.kind ('error' | 'notice')."""
    return "error" if alert.severity == "error" else "notice"


def event_payload(alert: Alert) -> dict[str, object]:
    """run_event.payload(jsonb)용 구조화 페이로드. None 필드는 제외."""
    payload: dict[str, object] = {"alert_kind": alert.kind, "title": alert.title}
    if alert.platform is not None:
        payload["platform"] = alert.platform
    if alert.error_class is not None:
        payload["error_class"] = alert.error_class
    if alert.error_raw is not None:
        payload["error_raw"] = alert.error_raw
    if alert.cause_line is not None:
        payload["cause_line"] = alert.cause_line
    if alert.context:
        payload["context"] = dict(alert.context)
    return payload
