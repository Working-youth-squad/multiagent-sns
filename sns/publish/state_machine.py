"""멱등 발행 상태머신 (FR-P3·P4, 07-발행 §2). C5.

    pending → container_created → published
       └──────────────┴──────────→ failed

동결된 `Publish` 툴 계약([sns.tools.contracts]) + `publish_attempt` 테이블 위에서
동작한다. 프레임워크·DB 무관: 부작용은 주입된 `publish`(발행)·`store`(영속)로만.

불변식:
- 이미 `published`면 툴을 호출하지 않는다 → 크래시 재시작 시 이중 발행 0 (FR-P3).
- 품질 게이트 미통과면 진입하지 않는다 (05 FR-Q, 07 §2).
- IG 2단계 컨테이너 ID를 보존·재사용한다 (재시작 복구).
- transient=재시도 여지 유지, 그 외=failed. 원문은 error_raw 보존 (FR-P4).
"""

from dataclasses import dataclass, replace
from typing import Literal, Protocol

from sns.tools.contracts import ErrorClass, MediaAsset, Platform, Publish

# publish_attempt.state CHECK와 동일 집합
AttemptStatus = Literal["pending", "container_created", "published", "failed"]

# 재시도 여지가 있는 오류 (그 외 auth/quota/spam_block/permanent_unknown = 종결)
RETRYABLE: frozenset[ErrorClass] = frozenset({"transient"})


class QualityGateError(RuntimeError):
    """품질 미통과 콘텐츠의 발행 진입 시도 (05 FR-Q)."""


@dataclass(frozen=True)
class PublishAttempt:
    publication_id: str
    state: AttemptStatus = "pending"
    # IG 2단계 컨테이너 ID (재시작 시 재사용)
    container_id: str | None = None
    error_class: ErrorClass | None = None
    error_raw: str | None = None
    # 성공 시 publication.external_post_id에 반영될 값
    external_post_id: str | None = None


class PublishAttemptStore(Protocol):
    """publish_attempt 영속 seam. 구현: 인메모리(테스트)·psycopg(운영, 후속)."""

    def load(self, publication_id: str) -> PublishAttempt | None: ...

    def save(self, attempt: PublishAttempt) -> None: ...


def run_publish(
    *,
    store: PublishAttemptStore,
    publish: Publish,
    publication_id: str,
    platform: Platform,
    media: MediaAsset,
    caption: str,
    idempotency_key: str,
    quality_passed: bool,
) -> PublishAttempt:
    """한 publication을 종결 상태까지 1회 전진시킨다. 여러 번 호출해도 안전(멱등)."""
    existing = store.load(publication_id)

    # FR-P3: 이미 발행됨 → 툴 미호출, 이중 발행 0
    if existing is not None and existing.state == "published":
        return existing

    # 05 FR-Q: 품질 게이트 미통과면 진입 금지 (pending으로도 들어가지 않음)
    if not quality_passed:
        raise QualityGateError(f"품질 미통과 발행 차단: {publication_id}")

    attempt = existing or PublishAttempt(publication_id=publication_id)

    # 재시작 복구: 보존된 컨테이너 ID를 넘겨 재사용
    result = publish(platform, media, caption, idempotency_key, container_id=attempt.container_id)

    if result.error is not None:
        container_id = result.container_id or attempt.container_id
        retryable = result.error.error_class in RETRYABLE
        if retryable:
            # 컨테이너가 생겼으면 그 진척을 보존(재시작 시 게시 단계부터)
            next_state: AttemptStatus = "container_created" if container_id else "pending"
        else:
            next_state = "failed"
        failed = replace(
            attempt,
            state=next_state,
            container_id=container_id,
            error_class=result.error.error_class,
            error_raw=result.error.error_raw,
        )
        store.save(failed)
        return failed

    published = replace(
        attempt,
        state="published",
        container_id=result.container_id or attempt.container_id,
        external_post_id=result.post_id,
        error_class=None,
        error_raw=None,
    )
    store.save(published)
    return published
