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


def test_quality_gate_skips_without_publishing(db: psycopg.Connection, seed: SeedFn) -> None:
    # 게이트 배선: quality_status가 passed가 아니면 발행 툴 미호출 + publication skipped.
    pub_id = seed(quality_status="failed")
    publish = FakePublish()
    results = run_pending_publications(db, publish)

    r = _only(results, pub_id)
    assert r.outcome == "skipped"
    assert r.attempt is None
    assert publish.calls == []
    assert _status(db, pub_id) == "skipped"
    assert _event_kinds(db) == ["notice"]


def test_needs_review_also_skipped(db: psycopg.Connection, seed: SeedFn) -> None:
    # 자동 게이트는 passed만 통과 — needs_review(사람 관문)도 자동 발행 진입 불가.
    pub_id = seed(quality_status="needs_review")
    publish = FakePublish()
    results = run_pending_publications(db, publish)
    assert _only(results, pub_id).outcome == "skipped"
    assert publish.calls == []


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
