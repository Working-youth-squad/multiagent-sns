"""챗봇 대화 영속화 — 인메모리 구현 (네트워크·DB 0)."""

import pytest

from sns.chat.store import ConversationNotFound, InMemoryChatStore


def test_append_and_read_back_in_order() -> None:
    store = InMemoryChatStore()
    cid = store.create_conversation()
    store.append(cid, role="user", body="개발자")
    store.append(cid, role="ranking", payload={"query": "개발자", "candidates": []})
    store.append(cid, role="assistant", body="세 소스에서 찾았습니다.")

    roles = [m.role for m in store.messages(cid)]
    assert roles == ["user", "ranking", "assistant"]
    assert [m.created_at for m in store.messages(cid)] == sorted(
        m.created_at for m in store.messages(cid)
    )


def test_ranking_payload_is_isolated_from_caller() -> None:
    """호출자가 나중에 dict를 고쳐도 저장분이 흔들리지 않는다(Pg 직렬화와 동작 일치)."""
    store = InMemoryChatStore()
    cid = store.create_conversation()
    payload: dict[str, object] = {"query": "개발자", "filter_mode": "active"}
    store.append(cid, role="ranking", payload=payload)

    payload["filter_mode"] = "off"

    saved = store.messages(cid)[0].payload
    assert saved is not None
    assert saved["filter_mode"] == "active"


def test_missing_conversation_raises() -> None:
    store = InMemoryChatStore()
    with pytest.raises(ConversationNotFound):
        store.append("없는-id", role="user", body="안녕")
    with pytest.raises(ConversationNotFound):
        store.messages("없는-id")
    with pytest.raises(ConversationNotFound):
        store.set_title("없는-id", "제목")
    assert store.get_conversation("없는-id") is None


def test_list_conversations_newest_first() -> None:
    store = InMemoryChatStore()
    first = store.create_conversation()
    second = store.create_conversation()
    store.set_title(first, "첫 대화")
    store.set_title(second, "둘째 대화")

    listed = store.list_conversations()
    assert [c.conversation_id for c in listed] == [second, first]
    assert listed[0].title == "둘째 대화"


def test_channel_binding_is_optional() -> None:
    """채널 없이 키워드만 탐색하는 사용도 1급이다(마이그 004 NULL 허용)."""
    store = InMemoryChatStore()
    loose = store.create_conversation()
    bound = store.create_conversation(channel_id="ch-1")

    assert store.get_conversation(loose) is not None
    assert store.get_conversation(loose).channel_id is None  # type: ignore[union-attr]
    assert store.get_conversation(bound).channel_id == "ch-1"  # type: ignore[union-attr]
