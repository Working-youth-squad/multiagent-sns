"""모니터링·알림 (C6, FR-W4) — Discord 웹훅 통지 + run_event 이중적재.

발신 지점은 `dispatch_alert(alert, sink=..., sender=..., model=...)` 하나. 알림은
[alerts]의 팩토리로 만들고([discord])로 보내며([dispatch])로 적재한다. 외부 장애
(LLM·웹훅·DB)는 모두 dispatch에서 삼켜, 알림이 발행 파이프라인을 막지 않는다.
"""

from sns.notify.alerts import (
    Alert,
    AlertKind,
    Severity,
    cycle_error,
    event_kind,
    event_payload,
    publish_failure,
    publish_success,
    quality_blocked,
    quota_exceeded,
    token_expiry,
)
from sns.notify.cause import analyze_cause
from sns.notify.discord import (
    ENV_DISCORD_WEBHOOK_URL,
    DiscordError,
    WebhookSender,
    discord_payload,
    discord_sender,
    discord_sender_from_env,
    post_discord,
)
from sns.notify.dispatch import (
    AlertSink,
    DispatchResult,
    InMemoryAlertSink,
    PgAlertSink,
    dispatch_alert,
)

__all__ = [
    "ENV_DISCORD_WEBHOOK_URL",
    "Alert",
    "AlertKind",
    "AlertSink",
    "DiscordError",
    "DispatchResult",
    "InMemoryAlertSink",
    "PgAlertSink",
    "Severity",
    "WebhookSender",
    "analyze_cause",
    "cycle_error",
    "discord_payload",
    "discord_sender",
    "discord_sender_from_env",
    "dispatch_alert",
    "event_kind",
    "event_payload",
    "post_discord",
    "publish_failure",
    "publish_success",
    "quality_blocked",
    "quota_exceeded",
    "token_expiry",
]
