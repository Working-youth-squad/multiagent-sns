"""공용 feedr 레이아웃(sns/web/layout.py) — 사이드바·bare 모드·불변식."""

from sns.web.layout import page


def test_sidebar_has_four_nav_links_with_absolute_urls() -> None:
    html = page("t", "<p>hi</p>", active="queue")
    for label, url in (
        ("새 포스트", "http://127.0.0.1:8002/compose"),
        ("AI 어시스턴트", "http://127.0.0.1:8003/"),
        ("대기열", "http://127.0.0.1:8001/"),
        ("채널", "http://127.0.0.1:8002/channels"),
    ):
        assert label in html
        assert f'href="{url}"' in html


def test_active_key_marks_exactly_one_nav_item() -> None:
    html = page("t", "", active="chat")
    assert html.count('nav-item active') == 1
    assert 'nav-item active" href="http://127.0.0.1:8003/"' in html


def test_bare_mode_has_no_sidebar() -> None:
    html = page("t", "<p>wizard</p>", active=None)
    assert "<aside" not in html
    assert 'class="bare"' in html
    assert "<p>wizard</p>" in html


def test_no_script_tag_and_title_escaped() -> None:
    html = page("<script>x</script>", "", active="channels")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_extra_css_and_max_width_injected() -> None:
    html = page("t", "", active="queue", extra_css=".mine{color:red}", max_width="680px")
    assert ".mine{color:red}" in html
    assert "max-width:680px" in html
