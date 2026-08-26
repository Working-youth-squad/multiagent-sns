"""키워드 챗봇 대화 영속화 (마이그 004, FR-W6 [신설]).

`ChatStore`가 앱이 의존하는 유일한 계약이고, 인메모리·Pg 두 구현이 그걸 만족한다
([sns.web.approve.store]·[sns.onboarding.store] 규율 동형 — 테스트는 DB 없이 돈다).

**메시지는 append-only다.** 수정 API가 없는 이유는 대화가 근거이기 때문이다: 어떤
랭킹을 보고 어떤 주제를 확정했는지가 나중에 발행물의 출처가 된다. 지우고 고칠 수
있으면 그 사슬이 끊긴다(run_event·channel_profile과 같은 규율).

`ranking` 역할이 따로 있는 것이 이 모듈에서 가장 중요한 결정이다 — LLM이 랭킹 숫자를
문장으로 옮겨 적으면 `rank_std=None`("불일치를 잴 수 없다")이 0.0("불일치가 없다")으로,
`filter_mode` 3값이 뭉개져 "필터 없는 척"이 된다. 그래서 랭킹은 LLM을 거치지 않고
`ranking_to_dict` 산출 그대로 박제하고, 화면이 그 원본을 그린다.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

import psycopg
from psycopg.types.json import Json

MessageRole = Literal["user", "assistant", "ranking", "system"]
"""마이그 004 CHECK 제약과 같은 값 집합. 화면 좌우와 LLM 이력 복원을 동시에 가른다."""

ROLES: tuple[MessageRole, ...] = ("user", "assistant", "ranking", "system")


class ConversationNotFound(LookupError):
    """대화 id가 없다 — 링크를 직접 친 경우/삭제된 대화."""


@dataclass(frozen=True)
class ChatMessage:
    message_id: str
    role: MessageRole
    body: str
    payload: Mapping[str, object] | None
    created_at: datetime


@dataclass(frozen=True)
class Conversation:
    conversation_id: str
    channel_id: str | None
    title: str | None
    created_at: datetime


class ChatStore(Protocol):
    """챗봇 앱이 의존하는 유일한 영속화 계약."""

    def create_conversation(self, *, channel_id: str | None = None) -> str: ...

    def get_conversation(self, conversation_id: str) -> Conversation | None: ...

    def list_conversations(self, *, limit: int = 50) -> tuple[Conversation, ...]: ...

    def set_title(self, conversation_id: str, title: str) -> None: ...

    def append(
        self,
        conversation_id: str,
        *,
        role: MessageRole,
        body: str = "",
        payload: Mapping[str, object] | None = None,
    ) -> str: ...

    def messages(self, conversation_id: str) -> tuple[ChatMessage, ...]: ...


@dataclass
class InMemoryChatStore:
    """결정론 테스트용. 시간은 단조 증가만 보장하면 되므로 호출 순서로 만든다."""

    conversations: dict[str, Conversation] = field(default_factory=dict)
    thread: dict[str, list[ChatMessage]] = field(default_factory=dict)
    tick: int = 0

    def _now(self) -> datetime:
        # 같은 밀리초에 두 메시지가 들어가도 정렬이 뒤집히지 않게 호출마다 1초씩 민다.
        self.tick += 1
        return datetime.fromtimestamp(self.tick, tz=UTC)

    def create_conversation(self, *, channel_id: str | None = None) -> str:
        conversation_id = str(uuid4())
        self.conversations[conversation_id] = Conversation(
            conversation_id=conversation_id,
            channel_id=channel_id,
            title=None,
            created_at=self._now(),
        )
        self.thread[conversation_id] = []
        return conversation_id

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        return self.conversations.get(conversation_id)

    def list_conversations(self, *, limit: int = 50) -> tuple[Conversation, ...]:
        ordered = sorted(self.conversations.values(), key=lambda c: c.created_at, reverse=True)
        return tuple(ordered[:limit])

    def set_title(self, conversation_id: str, title: str) -> None:
        current = self.conversations.get(conversation_id)
        if current is None:
            raise ConversationNotFound(conversation_id)
        self.conversations[conversation_id] = Conversation(
            conversation_id=current.conversation_id,
            channel_id=current.channel_id,
            title=title,
            created_at=current.created_at,
        )

    def append(
        self,
        conversation_id: str,
        *,
        role: MessageRole,
        body: str = "",
        payload: Mapping[str, object] | None = None,
    ) -> str:
        if conversation_id not in self.conversations:
            raise ConversationNotFound(conversation_id)
        message_id = str(uuid4())
        self.thread[conversation_id].append(
            ChatMessage(
                message_id=message_id,
                role=role,
                body=body,
                # 참조를 그대로 들면 호출자가 나중에 고칠 수 있다. Pg가 직렬화로 얻는
                # 격리를 여기서도 만들어 둬야 두 구현의 동작이 갈리지 않는다.
                payload=None if payload is None else json.loads(json.dumps(dict(payload))),
                created_at=self._now(),
            )
        )
        return message_id

    def messages(self, conversation_id: str) -> tuple[ChatMessage, ...]:
        if conversation_id not in self.conversations:
            raise ConversationNotFound(conversation_id)
        return tuple(self.thread[conversation_id])


class PgChatStore:
    """psycopg 백엔드. autocommit 커넥션을 주입받는다([sns.publish.stores] 규율)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def create_conversation(self, *, channel_id: str | None = None) -> str:
        row = self._conn.execute(
            "INSERT INTO chat_conversation (channel_id) VALUES (%s) RETURNING id",
            (channel_id,),
        ).fetchone()
        assert row is not None
        return str(row[0])

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        row = self._conn.execute(
            "SELECT id, channel_id, title, created_at FROM chat_conversation WHERE id = %s",
            (conversation_id,),
        ).fetchone()
        return None if row is None else _conversation(row)

    def list_conversations(self, *, limit: int = 50) -> tuple[Conversation, ...]:
        rows = self._conn.execute(
            "SELECT id, channel_id, title, created_at FROM chat_conversation "
            "ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return tuple(_conversation(r) for r in rows)

    def set_title(self, conversation_id: str, title: str) -> None:
        row = self._conn.execute(
            "UPDATE chat_conversation SET title = %s WHERE id = %s RETURNING id",
            (title, conversation_id),
        ).fetchone()
        if row is None:
            raise ConversationNotFound(conversation_id)

    def append(
        self,
        conversation_id: str,
        *,
        role: MessageRole,
        body: str = "",
        payload: Mapping[str, object] | None = None,
    ) -> str:
        # FK 위반을 psycopg 예외로 받지 않고 먼저 확인한다 — 사유를 계약 예외로 돌려주는
        # 편이 인메모리 구현과 동작이 같고, autocommit 커넥션이 예외로 오염되지 않는다.
        if self.get_conversation(conversation_id) is None:
            raise ConversationNotFound(conversation_id)
        row = self._conn.execute(
            "INSERT INTO chat_message (conversation_id, role, body, payload) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (conversation_id, role, body, None if payload is None else Json(dict(payload))),
        ).fetchone()
        assert row is not None
        return str(row[0])

    def messages(self, conversation_id: str) -> tuple[ChatMessage, ...]:
        if self.get_conversation(conversation_id) is None:
            raise ConversationNotFound(conversation_id)
        rows = self._conn.execute(
            "SELECT id, role, body, payload, created_at FROM chat_message "
            "WHERE conversation_id = %s ORDER BY created_at, id",
            (conversation_id,),
        ).fetchall()
        return tuple(
            ChatMessage(
                message_id=str(r[0]),
                role=r[1],
                body=str(r[2]),
                payload=r[3],
                created_at=r[4],
            )
            for r in rows
        )


def _conversation(row: Sequence[object]) -> Conversation:
    return Conversation(
        conversation_id=str(row[0]),
        channel_id=None if row[1] is None else str(row[1]),
        title=None if row[2] is None else str(row[2]),
        created_at=row[3],  # type: ignore[arg-type]
    )


# mypy(sns): 두 구현이 계약 ChatStore를 구조적으로 만족함을 강제.
_check_inmemory: ChatStore = InMemoryChatStore()


def _check_pg(store: PgChatStore) -> ChatStore:
    return store
