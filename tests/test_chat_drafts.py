"""시드 사이클 결과 페이로드 + 초안 카드 렌더 (FR-W6 · FR-W5). 네트워크·DB 0."""

from sns.chat.drafts import (
    BODY_PREVIEW_CHARS,
    SEED_DONE,
    DraftItem,
    SeedOutcome,
    seed_done_message,
    seed_done_payload,
)
from sns.chat.store import ChatMessage, Conversation
from sns.web.chat.render import render_conversation, render_drafts
from tests.test_chat_app import _now

_APPROVE = "http://127.0.0.1:8001"


def _prepared(**overrides: object) -> DraftItem:
    base: dict[str, object] = {
        "channel_label": "instagram @demo",
        "outcome": "prepared",
        "content_item_id": "ci-1",
        "body": "훅: 포트폴리오 시작이 반이다",
        "media_asset_id": "ma-1",
        "content_status": "needs_review",
        "quality_status": "passed",
    }
    base.update(overrides)
    return DraftItem(**base)  # type: ignore[arg-type]


def _outcome(*items: DraftItem, status: str = "completed") -> SeedOutcome:
    return SeedOutcome(
        cycle_id="cy-1", status=status, topic_title="개발자 포트폴리오 작성법", items=items
    )


# ── 페이로드 계약 ──────────────────────────────────────────────────────


def test_payload_carries_what_the_screen_needs() -> None:
    payload = seed_done_payload(_outcome(_prepared()), approve_base=_APPROVE)
    assert payload["kind"] == SEED_DONE
    assert payload["prepared_count"] == 1
    item = payload["items"][0]  # type: ignore[index]
    assert item["approve_url"] == "http://127.0.0.1:8001/items/ci-1"
    assert item["media_asset_id"] == "ma-1"
    assert item["content_status"] == "needs_review"


def test_approve_url_only_for_prepared_items() -> None:
    """차단·실패 건에 승인 링크를 걸면 없는 화면으로 보낸다."""
    payload = seed_done_payload(
        _outcome(_prepared(outcome="blocked", error="금지 소재")), approve_base=_APPROVE
    )
    assert payload["items"][0]["approve_url"] is None  # type: ignore[index]


def test_trailing_slash_in_base_does_not_double() -> None:
    payload = seed_done_payload(_outcome(_prepared()), approve_base="http://host:8001/")
    assert payload["items"][0]["approve_url"] == "http://host:8001/items/ci-1"  # type: ignore[index]


def test_body_is_truncated_but_original_length_survives() -> None:
    long_body = "가" * (BODY_PREVIEW_CHARS + 250)
    payload = seed_done_payload(_outcome(_prepared(body=long_body)), approve_base=_APPROVE)
    item = payload["items"][0]  # type: ignore[index]
    assert len(item["body_preview"]) == BODY_PREVIEW_CHARS
    # 잘렸다는 사실을 화면이 밝힐 수 있어야 한다 — 요약해 놓고 전문인 척하면 안 된다.
    assert item["body_length"] == len(long_body)


def test_message_line_survives_without_the_card() -> None:
    assert "초안 1건" in seed_done_message(_outcome(_prepared()))
    assert "대상이 없습니다" in seed_done_message(_outcome())
    failed = seed_done_message(_outcome(_prepared(outcome="failed", error="렌더 실패")))
    assert "렌더 실패" in failed


# ── 화면 ───────────────────────────────────────────────────────────────


def test_card_shows_approval_state_not_media_quality() -> None:
    """quality=passed여도 사람 승인 전이면 나가지 않는다 — 품질만 보이면 발행된 줄 안다."""
    html = render_drafts(seed_done_payload(_outcome(_prepared()), approve_base=_APPROVE))
    assert "승인 대기" in html
    assert "품질 통과" not in html
    assert 'src="/media/ma-1"' in html
    assert "http://127.0.0.1:8001/items/ci-1" in html


def test_failing_quality_is_added_next_to_approval_state() -> None:
    html = render_drafts(
        seed_done_payload(_outcome(_prepared(quality_status="failed")), approve_base=_APPROVE)
    )
    assert "승인 대기" in html and "품질 미달" in html


def test_blocked_item_shows_reason_not_a_link() -> None:
    html = render_drafts(
        seed_done_payload(
            _outcome(_prepared(outcome="blocked", error="금지 소재(piracy) — '크랙'")),
            approve_base=_APPROVE,
        )
    )
    assert "게이트 차단" in html
    assert "크랙" in html
    assert "/items/ci-1" not in html


def test_missing_media_renders_placeholder_not_broken_image() -> None:
    html = render_drafts(
        seed_done_payload(_outcome(_prepared(media_asset_id=None)), approve_base=_APPROVE)
    )
    assert "이미지 없음" in html
    assert "<img" not in html


def test_truncation_is_disclosed_on_screen() -> None:
    long_body = "가" * (BODY_PREVIEW_CHARS + 60)
    html = render_drafts(
        seed_done_payload(_outcome(_prepared(body=long_body)), approve_base=_APPROVE)
    )
    assert "이하 60자는 승인 화면에서" in html


def test_no_target_says_what_is_missing() -> None:
    html = render_drafts(seed_done_payload(_outcome(), approve_base=_APPROVE))
    assert "hybrid" in html


def test_draft_payload_is_escaped() -> None:
    html = render_drafts(
        seed_done_payload(
            _outcome(_prepared(body="<script>alert(1)</script>")), approve_base=_APPROVE
        )
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_conversation_renders_draft_card_instead_of_plain_bubble() -> None:
    """system 메시지지만 seed_done 페이로드가 붙으면 카드로 그린다."""
    payload = seed_done_payload(_outcome(_prepared()), approve_base=_APPROVE)
    conversation = Conversation(
        conversation_id="c1", channel_id=None, title="포트폴리오", created_at=_now()
    )
    messages = (
        ChatMessage(
            message_id="m1",
            role="system",
            body=seed_done_message(_outcome(_prepared())),
            payload=payload,
            created_at=_now(),
        ),
    )
    html = render_conversation(conversation, messages)
    assert 'class="draft"' in html
    assert 'src="/media/ma-1"' in html


def test_plain_system_message_stays_a_bubble() -> None:
    conversation = Conversation(
        conversation_id="c1", channel_id=None, title="포트폴리오", created_at=_now()
    )
    messages = (
        ChatMessage(
            message_id="m1",
            role="system",
            body="초안 제작을 시작했습니다.",
            payload={"kind": "seed_started"},
            created_at=_now(),
        ),
    )
    html = render_conversation(conversation, messages)
    assert 'class="draft"' not in html
    assert "초안 제작을 시작했습니다." in html
