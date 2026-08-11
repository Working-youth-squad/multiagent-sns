"""PublishAttemptStore 구현.

- `InMemoryPublishAttemptStore`: 결정론 테스트·러너 드라이런용 (NFR-2).
- psycopg 백엔드(publish_attempt + publication 갱신)는 러너(C5 후반)에서 추가.
"""

from sns.publish.state_machine import PublishAttempt


class InMemoryPublishAttemptStore:
    """publication_id → PublishAttempt 인메모리 원장."""

    def __init__(self) -> None:
        self._data: dict[str, PublishAttempt] = {}

    def load(self, publication_id: str) -> PublishAttempt | None:
        return self._data.get(publication_id)

    def save(self, attempt: PublishAttempt) -> None:
        self._data[attempt.publication_id] = attempt
