"""발행 러너 통합 검증 — 품질 게이트 배선 + 멱등 상태머신 구동 + 채널 격리 (C5 후속)."""

import psycopg

from sns.publish.runner import RunnerResult, run_pending_publications
from sns.tools.contracts import MediaAsset, Platform, PublishResult, ToolError
from sns.tools.fakes import FakePublish
from tests.conftest import SeedFn


def _status(db: psycopg.Connection, pub_id: str) -> str:
    row = db.execute("SELECT status FROM publication WHERE id = %s", (pub_id,)).fetchone()
    assert row is not None
    return row[0]


def _event_kinds(db: psycopg.Connection) -> list[str]:
    return [r[0] for r in db.execute("SELECT kind FROM run_event ORDER BY created_at").fetchall()]


def _only(results: list[RunnerResult], pub_id: str) -> RunnerResult:
    match = [r for r in results if r.publication_id == pub_id]
    assert len(match) == 1
    return match[0]


def test_passed_publishes_and_records(db: psycopg.Connection, seed: SeedFn) -> None:
    pub_id = seed(quality_status="passed")
    publish = FakePublish()
    results = run_pending_publications(db, publish)

    r = _only(results, pub_id)
    assert r.outcome == "published"
    assert r.attempt is not None and r.attempt.external_post_id is not None
    assert publish.calls == [pub_id]  # idempotency_key = publication_id
    assert _status(db, pub_id) == "published"
    assert _event_kinds(db) == ["publish_attempted"]


def test_quality_failed_skips_without_publishing(db: psycopg.Connection, seed: SeedFn) -> None:
    # 자동 게이트 하드 실패(failed)만 skipped로 종결 — 발행 툴 미호출.
    pub_id = seed(quality_status="failed")
    publish = FakePublish()
    results = run_pending_publications(db, publish)

    r = _only(results, pub_id)
    assert r.outcome == "skipped"
    assert r.attempt is None
    assert publish.calls == []
    assert _status(db, pub_id) == "skipped"
    assert _event_kinds(db) == ["notice"]


def test_needs_review_stays_pending_for_human_approval(
    db: psycopg.Connection, seed: SeedFn
) -> None:
    # needs_review는 hybrid 사람 관문(FR-Q3) 대기 — 자동 발행 진입은 막되 skipped로
    # 종결하지 않는다(그러면 승인돼도 재선택 안 돼 영영 발행 안 됨).
    pub_id = seed(quality_status="needs_review")
    publish = FakePublish()
    results = run_pending_publications(db, publish)

    assert _only(results, pub_id).outcome == "awaiting_review"
    assert publish.calls == []
    assert _status(db, pub_id) == "pending"  # skipped 아님 — 승인 대기


def test_needs_review_then_approved_publishes(db: psycopg.Connection, seed: SeedFn) -> None:
    # 사람이 승인해 quality_status가 passed로 바뀌면 다음 구동에서 실제 발행된다.
    pub_id = seed(quality_status="needs_review")
    publish = FakePublish()
    run_pending_publications(db, publish)
    assert _status(db, pub_id) == "pending"

    db.execute(
        "UPDATE media_asset SET quality_status = 'passed' WHERE quality_status = 'needs_review'"
    )
    results = run_pending_publications(db, publish)

    assert _only(results, pub_id).outcome == "published"
    assert publish.calls == [pub_id]
    assert _status(db, pub_id) == "published"


def test_no_matching_media_stays_pending(db: psycopg.Connection, seed: SeedFn) -> None:
    pub_id = seed(with_media=False)
    publish = FakePublish()
    results = run_pending_publications(db, publish)

    r = _only(results, pub_id)
    assert r.outcome == "no_media"
    assert publish.calls == []
    # 발행 불가지만 skipped로 종결하지 않는다 — 다음 렌더 후 재선택되도록 pending 유지.
    assert _status(db, pub_id) == "pending"


def test_idempotent_published_not_reselected(db: psycopg.Connection, seed: SeedFn) -> None:
    # FR-P3: 재구동 시 이미 published된 건은 다시 발행 시도되지 않는다.
    pub_id = seed(quality_status="passed")
    publish = FakePublish()
    run_pending_publications(db, publish)
    second = run_pending_publications(db, publish)

    assert [r for r in second if r.publication_id == pub_id] == []
    assert publish.calls == [pub_id]  # 두 번째 구동은 발행 툴을 부르지 않음


def test_transient_error_stays_retryable(db: psycopg.Connection, seed: SeedFn) -> None:
    pub_id = seed(quality_status="passed")
    publish = FakePublish(error=ToolError("transient", "5xx from API"))
    results = run_pending_publications(db, publish)

    assert _only(results, pub_id).outcome == "retryable"
    # 재시도 여지 유지 — publication은 pending, 다음 구동에서 재선택된다.
    assert _status(db, pub_id) == "pending"
    again = run_pending_publications(db, publish)
    assert _only(again, pub_id).outcome == "retryable"
    assert publish.calls == [pub_id, pub_id]  # 두 번 다 재시도


def test_in_progress_attempt_not_regated_by_newer_failed_asset(
    db: psycopg.Connection, seed: SeedFn
) -> None:
    # 회귀(#1): container_created로 진행 중인데 그 사이 더 최신 저품질 자산이 렌더되면,
    # 러너가 게이트로 publication을 skipped 뒤집으면 안 된다 — 컨테이너를 남긴 채
    # 원장(container_created)↔발행상태(skipped)가 갈라진다. 상태머신 위임으로 이어가야 함.
    pub_id = seed(quality_status="passed", kind="video", fmt="reels", checksum="a1")
    ci_row = db.execute(
        "SELECT content_item_id FROM publication WHERE id = %s", (pub_id,)
    ).fetchone()
    assert ci_row is not None
    ci_id = ci_row[0]
    # 진행 중 시도 주입(컨테이너 보존) — store 규칙상 publication은 pending 유지.
    db.execute(
        "INSERT INTO publish_attempt (publication_id, state, container_id)"
        " VALUES (%s, 'container_created', 'ig-c1')",
        (pub_id,),
    )
    # 더 최신의 저품질 자산 — SELECT의 DISTINCT ON이 이 행을 잡게 된다.
    db.execute(
        "INSERT INTO media_asset"
        " (content_item_id, kind, storage_url, checksum, quality_status, created_at)"
        " VALUES (%s, 'video', 'mem://a2', 'a2', 'failed', now() + interval '1 second')",
        (ci_id,),
    )

    publish = FakePublish()
    results = run_pending_publications(db, publish)

    r = _only(results, pub_id)
    assert r.outcome == "published"  # 게이트에 안 막히고 이어감
    assert publish.calls == [pub_id]  # run_publish에 실제 진입(컨테이너 재사용)
    assert _status(db, pub_id) == "published"  # skipped 아님 — 원장과 일관


class _SelectiveFail:
    """지정한 idempotency_key만 영구 실패(auth), 나머지는 성공 — 채널 격리 검증용."""

    def __init__(self, fail_ids: set[str]) -> None:
        self._fail_ids = fail_ids
        self.calls: list[str] = []

    def __call__(
        self,
        platform: Platform,
        media: MediaAsset,
        caption: str,
        idempotency_key: str,
        container_id: str | None = None,
    ) -> PublishResult:
        self.calls.append(idempotency_key)
        if idempotency_key in self._fail_ids:
            return PublishResult(error=ToolError("auth", "토큰 만료"))
        return PublishResult(post_id=f"post-{idempotency_key[:6]}")


def test_manual_channel_never_machine_published(db: psycopg.Connection, seed: SeedFn) -> None:
    # 발행 모드 3분류: manual(수동) 채널의 발행 건은 기계 발행 경로에 절대 들어가지
    # 않는다 — 사람이 직접 발행하고 sns.publish.manual로 등록만 한다(FR-E5).
    pub_id = seed(quality_status="passed", mode="manual")
    publish = FakePublish()
    results = run_pending_publications(db, publish)

    assert [r for r in results if r.publication_id == pub_id] == []
    assert publish.calls == []
    assert _status(db, pub_id) == "pending"


def test_off_channel_not_selected(db: psycopg.Connection, seed: SeedFn) -> None:
    # off = 발행 중지. 대기 건이 있어도 기계 발행하지 않는다.
    pub_id = seed(quality_status="passed", mode="off")
    results = run_pending_publications(db, FakePublish())
    assert [r for r in results if r.publication_id == pub_id] == []
    assert _status(db, pub_id) == "pending"


def test_publish_event_records_mode(db: psycopg.Connection, seed: SeedFn) -> None:
    # 증빙: publish_attempted 이벤트에 발행 모드가 남는다.
    seed(quality_status="passed", mode="hybrid")
    run_pending_publications(db, FakePublish())
    row = db.execute(
        "SELECT payload->>'mode' FROM run_event WHERE kind = 'publish_attempted'"
    ).fetchone()
    assert row is not None and row[0] == "hybrid"


def test_hybrid_not_approved_stays_pending_even_when_quality_passed(
    db: psycopg.Connection, seed: SeedFn
) -> None:
    # FR-Q3/C9: 품질 게이트가 passed를 내려도 hybrid는 content_item 승인이 없으면
    # 발행 진입하지 않는다 — 승인 UI 없이 자동 발행되는 구멍을 막는다.
    pub_id = seed(quality_status="passed", mode="hybrid", content_status="needs_review")
    publish = FakePublish()
    results = run_pending_publications(db, publish)

    r = _only(results, pub_id)
    assert r.outcome == "awaiting_review"
    assert publish.calls == []
    assert _status(db, pub_id) == "pending"


def test_hybrid_approved_and_quality_passed_publishes(db: psycopg.Connection, seed: SeedFn) -> None:
    pub_id = seed(quality_status="passed", mode="hybrid", content_status="approved")
    publish = FakePublish()
    results = run_pending_publications(db, publish)

    assert _only(results, pub_id).outcome == "published"
    assert publish.calls == [pub_id]
    assert _status(db, pub_id) == "published"


def test_hybrid_rejected_content_skips_regardless_of_quality(
    db: psycopg.Connection, seed: SeedFn
) -> None:
    # 사람 반려([sns.web.approve])는 미디어 품질과 무관하게 즉시 종결한다.
    pub_id = seed(quality_status="passed", mode="hybrid", content_status="rejected")
    publish = FakePublish()
    results = run_pending_publications(db, publish)

    r = _only(results, pub_id)
    assert r.outcome == "skipped"
    assert publish.calls == []
    assert _status(db, pub_id) == "skipped"


def test_channel_isolation(db: psycopg.Connection, seed: SeedFn) -> None:
    # FR-P4: 한 건의 영구 실패가 다른 건 발행을 막지 않는다.
    bad = seed(quality_status="passed", platform="instagram", checksum="bad")
    good = seed(
        quality_status="passed", platform="youtube", kind="video", fmt="shorts", checksum="good"
    )
    publish = _SelectiveFail(fail_ids={bad})

    run_pending_publications(db, publish)  # type: ignore[arg-type]

    assert _status(db, bad) == "failed"
    assert _status(db, good) == "published"
