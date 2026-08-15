"""Discord 웹훅 전송 (C6, FR-W4) — 순수 페이로드 렌더 + 얇은 transport.

페이로드 조립(`discord_payload`)은 순수 함수라 픽스처로 검증한다. 네트워크 접촉은
얇은 `post_discord`에 격리하고, 소켓 타임아웃으로 상한을 직접 강제한다 —
[sns.research.sources.google_trends]의 opener 주입 패턴과 동형(테스트에서 네트워크
대신 픽스처 응답 주입).

웹훅 URL은 env `DISCORD_WEBHOOK_URL`. 미설정이면 `discord_sender_from_env()`가 None을
반환하고, dispatch는 전송을 건너뛴 채 DB 적재만 한다 — 알림 경로는 그래도 산다.
"""

import json
import os
import urllib.request
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from sns.notify.alerts import Alert

ENV_DISCORD_WEBHOOK_URL = "DISCORD_WEBHOOK_URL"

# 임베드 색(십진 RGB): 실패=빨강, 그 외=초록.
_COLOR_ERROR = 0xD83C3C
_COLOR_INFO = 0x36A64F

# Discord 임베드 description 하드 한도는 4096자 — 넘기면 400을 받아 통지가 조용히
# 유실된다(delivered=False로 삼킴). error_raw뿐 아니라 임의 길이인 context 값,
# 그리고 최종 description 전체에 상한을 걸어 전송 실패를 원천 차단한다.
_RAW_MAX_CHARS = 600
_VALUE_MAX_CHARS = 600  # context 값 1개당 상한(한 필드가 나머지를 밀어내지 않게)
_DESC_MAX_CHARS = 4000  # description 전체 상한(Discord 4096 한도에 여유)


class DiscordError(RuntimeError):
    """웹훅 전송 실패 — dispatch가 best-effort로 삼킨다(알림이 사이클을 막지 않게)."""


class _Response(Protocol):
    status: int

    def read(self, amt: int = ..., /) -> bytes: ...


# urllib 계열 opener 주입점(테스트에서 네트워크 대신 픽스처 응답 주입).
Opener = Callable[..., AbstractContextManager[_Response]]

# dispatch가 부르는 전송 seam: 렌더된 페이로드를 받아 보낸다. 실패는 예외로 던진다.
WebhookSender = Callable[[dict[str, object]], None]


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def discord_payload(alert: Alert) -> dict[str, object]:
    """알림 → Discord 웹훅 JSON(임베드 1개). 순수 함수.

    분류(error_class)는 항상 실린다 — LLM 원인(cause_line)이 없어도 분류명만으로
    통지되게 하는 FR-W4의 '분류명만이라도 발송' 보증이 여기서 성립한다.
    """
    lines: list[str] = []
    if alert.error_class is not None:
        lines.append(f"분류: {alert.error_class}")
    if alert.cause_line:
        lines.append(f"원인: {alert.cause_line}")
    if alert.error_raw:
        lines.append(f"원문: {_truncate(alert.error_raw, _RAW_MAX_CHARS)}")
    for key, value in sorted(alert.context.items()):
        lines.append(f"{key}: {_truncate(value, _VALUE_MAX_CHARS)}")

    embed: dict[str, object] = {
        "title": alert.title,
        "color": _COLOR_ERROR if alert.severity == "error" else _COLOR_INFO,
    }
    if lines:
        # 우선순위 높은 줄(분류·원인·원문)이 앞이라 초과 시 뒤(context)부터 잘린다.
        embed["description"] = _truncate("\n".join(lines), _DESC_MAX_CHARS)
    return {"embeds": [embed]}


def post_discord(
    payload: dict[str, object],
    *,
    webhook_url: str,
    timeout_s: float = 10.0,
    opener: Opener = urllib.request.urlopen,
) -> None:
    """페이로드를 웹훅으로 POST. 비-2xx/네트워크 오류는 `DiscordError`로 전파."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener(request, timeout=timeout_s) as resp:
            status = resp.status
    except DiscordError:
        raise
    except Exception as exc:  # URLError·HTTPError·소켓 타임아웃 등 — 전송 실패로 일원화
        raise DiscordError(f"Discord 웹훅 전송 실패: {exc}") from exc
    if status < 200 or status >= 300:
        raise DiscordError(f"Discord 웹훅 비정상 응답: {status}")


def discord_sender(
    webhook_url: str,
    *,
    timeout_s: float = 10.0,
    opener: Opener = urllib.request.urlopen,
) -> WebhookSender:
    """URL을 고정한 `WebhookSender` 클로저 — dispatch에 주입한다."""

    def send(payload: dict[str, object]) -> None:
        post_discord(payload, webhook_url=webhook_url, timeout_s=timeout_s, opener=opener)

    return send


def discord_sender_from_env(
    env: dict[str, str] | None = None, *, timeout_s: float = 10.0
) -> WebhookSender | None:
    """env `DISCORD_WEBHOOK_URL`에서 sender 배선. 미설정이면 None(전송 생략)."""
    source = os.environ if env is None else env
    url = source.get(ENV_DISCORD_WEBHOOK_URL)
    if not url:
        return None
    return discord_sender(url, timeout_s=timeout_s)
