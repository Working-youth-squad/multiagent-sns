"""공용 feedr 레이아웃(sns/web/layout.py) — 사이드바·bare 모드·불변식."""

from sns.web.layout import page


def test_sidebar_reads_bases_from_env_at_render_time(monkeypatch) -> None:
    """네비 URL은 렌더 시점 env — 진입 스크립트가 dotenv를 main()에서 로드해도 반영."""
    monkeypatch.setenv("ONBOARD_WEB_BASE", "http://onboard.test")
    monkeypatch.setenv("CHAT_WEB_BASE", "http://chat.test")
    monkeypatch.setenv("APPROVE_WEB_BASE", "http://web.test/queue")
    html = page("t", "<p>hi</p>", active="queue")
    for label, url in (
        ("새 포스트", "http://onboard.test/compose"),
        ("AI 어시스턴트", "http://chat.test/"),
        ("대기열", "http://web.test/queue/"),
        ("채널", "http://onboard.test/channels"),
    ):
        assert label in html
        assert f'href="{url}"' in html


def test_active_key_marks_exactly_one_nav_item(monkeypatch) -> None:
    monkeypatch.setenv("CHAT_WEB_BASE", "http://chat.test")
    html = page("t", "", active="chat")
    assert html.count("nav-item active") == 1
    assert 'nav-item active" href="http://chat.test/"' in html


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
