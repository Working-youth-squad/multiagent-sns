"""platform → 발행 어댑터 라우팅 (Capability Gate의 발행판).

[sns.render.video.router]와 같은 자리다: **"이 환경이 어디에 올릴 수 있는가"의 단일
출처**다. 라우터에 주입되지 않은 플랫폼은 지금 이 배선에서 올릴 수 없다 —
"올릴 수 없는 게시물"이 아니다.

렌더 라우터와 **다르게 예외를 던지지 않는다.** 발행 러너([sns.publish.runner])는
대기 건 전량을 한 루프에서 돌리고, 그 루프는 채널 격리가 규율이다(FR-P4: 한 건의
실패가 다른 건에 영향을 주지 않는다). 여기서 던지면 루프 자체가 끊겨, 배선된 플랫폼의
건들까지 그날 안 나간다 — 격리가 정확히 반대로 깨진다.

대신 **재시도 가능(transient) 오류**를 돌려준다. 상태머신이 그 건을 `pending`으로 두고
넘어가므로:
  · `failed`로 종결되지 않는다 — 어댑터를 배선하면 그대로 나간다
  · `skipped`로 종결되지 않는다 — 그건 품질 하드 실패·사람 반려의 자리다
  · 사유가 원장에 남는다 — 왜 안 나갔는지가 "아무 일도 없었음"으로 사라지지 않는다
"""

from collections.abc import Mapping

from sns.tools.contracts import (
    MediaAsset,
    Platform,
    Publish,
    PublishResult,
    ToolError,
)


class PlatformPublishRouter:
    """`Publish` 계약을 만족하면서 platform으로 갈라 보낸다."""

    def __init__(self, adapters: Mapping[Platform, Publish]) -> None:
        self._adapters = dict(adapters)

    @property
    def supported_platforms(self) -> tuple[Platform, ...]:
        return tuple(self._adapters)

    def __call__(
        self,
        platform: Platform,
        media: MediaAsset,
        caption: str,
        idempotency_key: str,
        container_id: str | None = None,
    ) -> PublishResult:
        adapter = self._adapters.get(platform)
        if adapter is None:
            return PublishResult(
                error=ToolError(
                    error_class="transient",
                    error_raw=(
                        f"이 배선에 {platform} 발행 어댑터가 없다 "
                        f"(배선된 곳: {sorted(self._adapters) or '없음'}) — "
                        "어댑터를 붙이면 이 건은 그대로 나간다"
                    ),
                )
            )
        return adapter(platform, media, caption, idempotency_key, container_id)
