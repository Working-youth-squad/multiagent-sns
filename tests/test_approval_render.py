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


VIDEO_ITEM = replace(
    ITEM,
    media_kind="video",
    content_format="shorts",
    media_spec={
        "topic": "고정 주제",
        "slides": [{"subtitle": "부제 <1>", "narration": "나레이션 한 문장."}],
    },
)


def test_render_detail_video_shows_cut_form_when_enabled() -> None:
    html = render_detail(VIDEO_ITEM, rerender_enabled=True)
    assert "/items/ci-1/rerender" in html
    assert 'name="topic"' in html and 'name="subtitle_0"' in html and 'name="narration_0"' in html
    assert "부제 &lt;1&gt;" in html  # 폼 값 이스케이프


def test_render_detail_video_form_hidden_without_wiring() -> None:
    assert "/items/ci-1/rerender" not in render_detail(VIDEO_ITEM)
    # 이미지 항목은 배선돼도 폼이 없다.
    assert "/items/ci-1/rerender" not in render_detail(ITEM, rerender_enabled=True)


def test_render_detail_error_banner_escaped() -> None:
    html = render_detail(VIDEO_ITEM, rerender_enabled=True, error="<너무 김>")
    assert "&lt;너무 김&gt;" in html and "<너무 김>" not in html


def test_render_detail_embeds_media_preview() -> None:
    assert "<video controls" in render_detail(VIDEO_ITEM)
    assert "/items/ci-1/media" in render_detail(VIDEO_ITEM)
    assert '<img class="preview"' in render_detail(ITEM)  # 이미지 항목
    no_media = replace(ITEM, media_storage_url=None)
    assert "/items/ci-1/media" not in render_detail(no_media)


def test_render_detail_notice_banner() -> None:
    html = render_detail(VIDEO_ITEM, notice="재렌더 완료 — 확인 후 승인하세요.")
    assert "재렌더 완료" in html


def test_render_list_groups_by_channel_and_filters() -> None:
    """대기열은 채널별로 분류된다 — 칩은 필터 링크, 기본은 채널별 섹션."""
    two = (ITEM, replace(ITEM, content_item_id="ci-2", handle="second"))
    html = render_list(two)
    assert "?channel=demo" in html and "?channel=second" in html  # 채널 칩
    assert html.count('<div class="card-list">') == 2  # 채널별 그룹 섹션
    only = render_list(two, selected="second")
    assert "/items/ci-2" in only and "/items/ci-1" not in only


def test_urls_respect_mount_prefix(monkeypatch) -> None:
    """통합 서버(run_web.py)가 /queue에 마운트할 때 내부 링크가 프리픽스를 따른다."""
    import importlib

    import sns.web.approve.render as render_mod

    monkeypatch.setenv("APPROVE_URL_PREFIX", "/queue")
    importlib.reload(render_mod)
    try:
        html = render_mod.render_list((ITEM,))
        assert "/queue/items/ci-1" in html
        detail = render_mod.render_detail(ITEM)
        assert 'action="/queue/items/ci-1/approve"' in detail
        assert 'href="/queue/"' in detail
    finally:
        monkeypatch.delenv("APPROVE_URL_PREFIX")
        importlib.reload(render_mod)  # 다른 테스트를 위해 기본값(빈 프리픽스) 복원


def test_render_not_found_links_back() -> None:
    html = render_not_found()
    assert "찾을 수 없습니다" in html
    assert 'href="/"' in html
