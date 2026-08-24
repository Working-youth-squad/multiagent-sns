"""승인 화면 HTML 렌더 — 순수 함수 검증(이스케이프·빈 목록·미리보기 절단)."""

from dataclasses import replace

from sns.web.approve.render import render_detail, render_list, render_not_found
from sns.web.approve.store import PendingItem

ITEM = PendingItem(
    content_item_id="ci-1",
    cycle_id="cy-1",
    topic_title="<script>alert(1)</script>",
    content_format="feed_image",
    hook_pattern="curiosity",
    body="본문 " * 100,
    media_asset_id="ma-1",
    media_kind="image",
    media_storage_url="mem://x",
    quality_status="needs_review",
    publication_id="pub-1",
    channel_id="ch-1",
    platform="instagram",
    handle="demo",
)


def test_render_list_empty_shows_empty_message() -> None:
    html = render_list(())
    assert "승인 대기 중인 항목이 없습니다" in html
    assert "<script>" not in html


def test_render_list_escapes_topic_and_links_to_detail() -> None:
    html = render_list((ITEM,))
    assert "&lt;script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "/items/ci-1" in html


def test_render_list_truncates_long_body() -> None:
    html = render_list((ITEM,))
    assert "…" in html


def test_render_detail_escapes_body_in_textarea() -> None:
    xss_item = replace(ITEM, body="</textarea><script>x</script>")
    html = render_detail(xss_item)
    assert "&lt;/textarea&gt;" in html
    assert "</textarea><script>" not in html


def test_render_detail_includes_approve_and_reject_forms() -> None:
    html = render_detail(ITEM)
    assert "/items/ci-1/approve" in html
    assert "/items/ci-1/reject" in html
    assert "hook=curiosity" in html


def test_render_not_found_links_back() -> None:
    html = render_not_found()
    assert "찾을 수 없습니다" in html
    assert 'href="/"' in html
