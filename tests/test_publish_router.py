"""플랫폼 발행 라우터 ([sns.publish.router]). 네트워크 0.

핵심은 **안 배선된 플랫폼이 종결되지 않는다**는 것이다. `failed`/`skipped`로 끝나면
어댑터를 붙인 뒤에도 그 건은 영영 다시 선택되지 않는다.
"""

import psycopg

from sns.publish.router import PlatformPublishRouter
from sns.publish.runner import run_pending_publications
from sns.tools.contracts import MediaAsset, Platform, PublishResult
from sns.tools.fakes import FakePublish
from tests.conftest import SeedFn

_MEDIA = MediaAsset(kind="video", storage_url="file:///x.mp4", checksum="c1")


def _call(router: PlatformPublishRouter, platform: Platform = "youtube") -> PublishResult:
    return router(platform, _MEDIA, "캡션", "idem-1")


def test_wired_platform_reaches_its_adapter() -> None:
    seen: list[Platform] = []

    def adapter(platform: Platform, *args: object, **kwargs: object) -> PublishResult:
        seen.append(platform)
        return PublishResult(post_id="yt-1")

    router = PlatformPublishRouter({"youtube": adapter})
    assert _call(router).post_id == "yt-1"
    assert seen == ["youtube"]


def test_unwired_platform_is_retryable_not_failed() -> None:
    """permanent로 돌려주면 어댑터를 붙인 뒤에도 그 건은 다시 선택되지 않는다."""
    result = _call(PlatformPublishRouter({}), platform="instagram")
    assert result.error is not None
    assert result.error.error_class == "transient"
    assert result.post_id is None


def test_unwired_reason_names_what_is_wired() -> None:
    """왜 안 나갔는지가 "아무 일도 없었음"으로 사라지지 않는다."""
    router = PlatformPublishRouter({"youtube": FakePublish()})
    result = _call(router, platform="instagram")
    assert result.error is not None
    assert "instagram" in result.error.error_raw
    assert "youtube" in result.error.error_raw


def test_supported_platforms_reports_the_wiring() -> None:
    assert PlatformPublishRouter({"youtube": FakePublish()}).supported_platforms == ("youtube",)
    assert PlatformPublishRouter({}).supported_platforms == ()


def test_router_does_not_raise_so_the_runner_loop_survives(
    db: psycopg.Connection, seed: SeedFn
) -> None:
    """한 건의 미배선이 다른 건의 발행을 막지 않는다(FR-P4 채널 격리).

    예전 배선은 단일 플랫폼 어댑터를 그대로 물려, 대기 큐에 다른 플랫폼이 섞이면
    `ValueError`로 루프가 끊겼다 — 배선된 플랫폼의 건까지 그날 안 나갔다.
    """
    ig = seed(platform="instagram", checksum="chk-ig")
    yt = seed(platform="youtube", checksum="chk-yt")

    router = PlatformPublishRouter({"youtube": FakePublish()})
    outcomes = {r.publication_id: r.outcome for r in run_pending_publications(db, router)}

    assert outcomes[yt] == "published"
    # 미배선 건은 종결되지 않는다 — 재시도 대기로 남아야 어댑터를 붙이면 나간다.
    assert outcomes[ig] not in ("published", "skipped")
    row = db.execute("SELECT status FROM publication WHERE id = %s", (ig,)).fetchone()
    assert row is not None
    assert row[0] == "pending"


def test_wrong_platform_adapter_is_never_reached() -> None:
    """유튜브 어댑터에 인스타 건을 넘기면 어댑터가 ValueError를 던진다 — 그 전에 막는다."""

    def strict(platform: Platform, *args: object, **kwargs: object) -> PublishResult:
        if platform != "youtube":
            raise ValueError("유튜브 어댑터가 처리할 수 없는 platform")
        return PublishResult(post_id="yt-1")

    router = PlatformPublishRouter({"youtube": strict})
    result = _call(router, platform="instagram")  # 던지지 않는다
    assert result.error is not None
