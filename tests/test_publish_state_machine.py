"""C5 발행 상태머신 검증 — 동결 계약(Publish) + FakePublish로 결정론 테스트."""

import pytest

from sns.publish.state_machine import (
    PublishAttempt,
    QualityGateError,
    run_publish,
)
from sns.publish.stores import InMemoryPublishAttemptStore
from sns.tools.contracts import MediaAsset, Platform, PublishResult, ToolError
from sns.tools.fakes import FakePublish, FakeRenderMedia

MEDIA: MediaAsset = FakeRenderMedia()({"template": "card"}, "image")


def _drive(store: InMemoryPublishAttemptStore, publish: object, *, quality_passed: bool = True):
    return run_publish(
        store=store,
        publish=publish,  # type: ignore[arg-type]
        publication_id="pub-1",
        platform="instagram",
        media=MEDIA,
        caption="훅 문장",
        idempotency_key="pub-1-ig",
        quality_passed=quality_passed,
    )


def test_success_reaches_published() -> None:
    store, publish = InMemoryPublishAttemptStore(), FakePublish()
    attempt = _drive(store, publish)
    assert attempt.state == "published"
    assert attempt.external_post_id is not None
    assert publish.calls == ["pub-1-ig"]
    assert store.load("pub-1") == attempt


def test_idempotent_no_double_publish() -> None:
    # FR-P3: 이미 published면 툴 미호출 (크래시 재시작 이중 발행 0)
    store, publish = InMemoryPublishAttemptStore(), FakePublish()
    first = _drive(store, publish)
    second = _drive(store, publish)
    assert first == second
    assert publish.calls == ["pub-1-ig"]  # 두 번째는 발행 툴을 부르지 않음


def test_quality_gate_blocks_entry() -> None:
    # 05 FR-Q: 미통과면 pending으로도 진입하지 않음 + 발행 툴 미호출
    store, publish = InMemoryPublishAttemptStore(), FakePublish()
    with pytest.raises(QualityGateError):
        _drive(store, publish, quality_passed=False)
    assert publish.calls == []
    assert store.load("pub-1") is None


def test_terminal_error_marks_failed_and_preserves_raw() -> None:
    # FR-P4: 영구 오류(quota)는 failed + 분류/원문 보존
    store = InMemoryPublishAttemptStore()
    publish = FakePublish(error=ToolError(error_class="quota", error_raw="rate limit exceeded"))
    attempt = _drive(store, publish)
    assert attempt.state == "failed"
    assert attempt.error_class == "quota"
    assert attempt.error_raw == "rate limit exceeded"
    assert attempt.external_post_id is None


def test_transient_error_stays_retryable_then_succeeds() -> None:
    # transient는 failed로 종결하지 않고 재시도 시 발행 성공
    store = InMemoryPublishAttemptStore()
    transient = FakePublish(error=ToolError(error_class="transient", error_raw="502"))
    first = _drive(store, transient)
    assert first.state == "pending"  # 재시도 여지 유지 (failed 아님)
    assert first.error_class == "transient"

    retry = _drive(store, FakePublish())  # 재시작·재시도
    assert retry.state == "published"
    assert retry.error_class is None


class _ScriptedPublish:
    """1회차 = 컨테이너 생성 후 transient 실패, 2회차 = 넘겨받은 컨테이너로 게시 성공.

    IG 2단계 재시작 복구(FR-P3 container 지점)를 재현한다.
    """

    def __init__(self) -> None:
        self.received_container_ids: list[str | None] = []
        self._calls = 0

    def __call__(
        self,
        platform: Platform,
        media: MediaAsset,
        caption: str,
        idempotency_key: str,
        container_id: str | None = None,
    ) -> PublishResult:
        self.received_container_ids.append(container_id)
        self._calls += 1
        if self._calls == 1:
            return PublishResult(
                container_id="ig-container-777",
                error=ToolError(error_class="transient", error_raw="publish step 503"),
            )
        return PublishResult(post_id="ig-post-999", container_id=container_id)


def test_container_created_resume_reuses_container_id() -> None:
    store, publish = InMemoryPublishAttemptStore(), _ScriptedPublish()

    after_container = _drive(store, publish)
    assert after_container.state == "container_created"
    assert after_container.container_id == "ig-container-777"

    published = _drive(store, publish)
    assert published.state == "published"
    assert published.external_post_id == "ig-post-999"
    # 2회차 호출이 보존된 컨테이너 ID를 재사용했다 (재시작 복구)
    assert publish.received_container_ids == [None, "ig-container-777"]


def test_load_starts_fresh_when_absent() -> None:
    store = InMemoryPublishAttemptStore()
    assert store.load("nope") is None
    store.save(PublishAttempt(publication_id="x", state="pending"))
    loaded = store.load("x")
    assert loaded is not None and loaded.state == "pending"
