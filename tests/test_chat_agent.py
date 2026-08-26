"""챗봇 대화 에이전트 — 툴 포착, LLM에 수치 미노출, 시드 확정. 네트워크 0.

`ScriptedChatModel`은 tests/test_content_agent.py가 세운 규율 그대로다.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from sns.chat.agent import (
    ChatTurn,
    run_chat_turn,
    summarize_for_model,
    to_langchain_messages,
)
from sns.chat.store import ChatMessage
from sns.research.keywords import aggregate
from sns.research.ranking import KeywordRanking
from sns.tools.contracts import SourceResult


class ScriptedChatModel(GenericFakeChatModel):
    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self


def _tool(name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "c1"}])


def _ranking(*, cross: bool = True) -> KeywordRanking:
    """실제 `aggregate` 산출을 쓴다 — 손으로 지은 모양이 아니라야 계약이 검증된다."""
    if cross:
        results = (
            SourceResult(
                source="naver_autocomplete", ok=True, items=("개발자 연봉", "개발자 취업")
            ),
            SourceResult(source="google_suggest", ok=True, items=("개발자 취업", "개발자 연봉")),
            SourceResult(source="youtube_suggest", ok=True, items=("개발자 연봉",)),
        )
    else:
        results = (
            SourceResult(source="naver_autocomplete", ok=True, items=("개발자 연봉",)),
            SourceResult(source="google_suggest", ok=False),
            SourceResult(source="youtube_suggest", ok=False),
        )
    return aggregate("개발자", results)


def _rank_fn(ranking: KeywordRanking) -> Callable[..., KeywordRanking]:
    def rank(query: str, **kwargs: Any) -> KeywordRanking:
        return ranking

    return rank


def _run(script: list[AIMessage], *, text: str = "개발자", **kwargs: Any) -> ChatTurn:
    return run_chat_turn(
        ScriptedChatModel(messages=iter(script)),
        history=kwargs.pop("history", ()),
        user_text=text,
        rank_fn=kwargs.pop("rank_fn", _rank_fn(_ranking())),
        **kwargs,
    )


# ── 툴 포착 ────────────────────────────────────────────────────────────


def test_search_tool_captures_full_ranking() -> None:
    turn = _run(
        [
            _tool("search_keywords", {"query": "개발자"}),
            AIMessage(content="세 소스에서 이런 키워드가 나왔습니다."),
        ]
    )
    assert turn.reply == "세 소스에서 이런 키워드가 나왔습니다."
    assert len(turn.rankings) == 1
    # 화면·DB가 쓰는 정본은 **원본 객체**다 — 요약본이 아니다.
    assert turn.rankings[0].query == "개발자"
    assert turn.rankings[0].candidates


def test_confirm_topic_produces_seed() -> None:
    turn = _run(
        [
            _tool(
                "confirm_topic",
                {"title": "개발자 연봉 협상 3가지", "summary": "연봉 협상 팁", "category": "tool"},
            ),
            AIMessage(content="초안을 만들게요."),
        ]
    )
    assert turn.seed_topic is not None
    assert turn.seed_topic.title == "개발자 연봉 협상 3가지"
    # 출처를 트렌드 소스 이름으로 적으면 자동 수집분과 구별되지 않는다.
    assert turn.seed_topic.source == "chat_seed"


def test_no_confirm_means_no_seed() -> None:
    turn = _run([AIMessage(content="어떤 쪽이 궁금하세요?")])
    assert turn.seed_topic is None
    assert turn.rankings == []


def test_confirm_topic_rejects_blank_fields() -> None:
    turn = _run(
        [
            _tool("confirm_topic", {"title": "  ", "summary": "내용"}),
            AIMessage(content="주제를 다시 말씀해주세요."),
        ]
    )
    assert turn.seed_topic is None


def test_rank_failure_keeps_conversation_alive() -> None:
    def boom(query: str, **kwargs: Any) -> KeywordRanking:
        raise RuntimeError("전 소스 실패")

    turn = _run(
        [
            _tool("search_keywords", {"query": "개발자"}),
            AIMessage(content="지금은 키워드를 못 가져왔습니다."),
        ],
        rank_fn=boom,
    )
    assert turn.rankings == []
    assert turn.reply  # 턴 자체는 완주한다


def test_empty_user_text_rejected() -> None:
    with pytest.raises(ValueError):
        _run([AIMessage(content="x")], text="   ")


# ── LLM에게 수치를 주지 않는다 (이 모듈의 핵심 규율) ──────────────────


def test_model_summary_carries_no_statistics() -> None:
    ranking = _ranking()
    summary = summarize_for_model(ranking)

    assert "개발자 연봉" in summary  # 키워드 이름은 준다
    assert "소스 3곳" in summary or "소스 2곳" in summary  # 정수 사실은 준다
    # 수치를 주면 LLM이 None을 0으로 옮겨 적는 실패가 가능해진다.
    for candidate in ranking.candidates:
        if candidate.rank_std is not None:
            assert f"{candidate.rank_std:.4f}" not in summary
        assert f"{candidate.observed_mean:.4f}" not in summary


def test_model_summary_states_filter_mode_precisely() -> None:
    passthrough = summarize_for_model(_ranking(cross=False))
    assert "필터가 열리지 않았습니다" in passthrough
    assert "걸러낸 결과" not in passthrough


def test_model_summary_names_failed_sources() -> None:
    summary = summarize_for_model(_ranking(cross=False))
    assert "google_suggest" in summary and "youtube_suggest" in summary


# ── 이력 복원 ──────────────────────────────────────────────────────────


def _msg(role: str, body: str = "", payload: dict | None = None) -> ChatMessage:
    return ChatMessage(
        message_id="m",
        role=role,  # type: ignore[arg-type]
        body=body,
        payload=payload,
        created_at=datetime.now(tz=UTC),
    )


def test_history_excludes_ranking_numbers_and_system_noise() -> None:
    history = [
        _msg("user", "개발자"),
        _msg("ranking", payload={"query": "개발자", "candidates": [{"rank_std": 0.1234}]}),
        _msg("assistant", "이런 키워드가 있어요"),
        _msg("system", "초안 제작을 시작했습니다"),
    ]
    restored = to_langchain_messages(history)
    text = "\n".join(str(m.content) for m in restored)

    assert "0.1234" not in text  # 수치를 모델 손에 되돌려주지 않는다
    assert "초안 제작을 시작했습니다" not in text  # system은 대화가 아니다
    assert "개발자" in text
    assert "키워드 표를 사용자에게 보여줬다" in text  # 무엇을 검색했는지는 잃지 않는다


def test_history_is_capped() -> None:
    from sns.chat.agent import HISTORY_LIMIT

    history = [_msg("user", f"메시지{i}") for i in range(HISTORY_LIMIT * 2)]
    assert len(to_langchain_messages(history)) == HISTORY_LIMIT
