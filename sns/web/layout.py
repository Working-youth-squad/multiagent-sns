"""공용 feedr 레이아웃 — 세 웹앱(:8001 승인 · :8002 온보딩 · :8003 챗봇)이 같은
토큰·사이드바를 쓴다.

디자인 원본은 feedr-clone의 사용자 화면(src/app/*)이며, 인라인 style 객체를
CSS 클래스로 승격한 것이다. 레포 규율 유지: 템플릿 엔진·정적 에셋·JS 없음,
CSS는 인라인 <style> 문자열([sns.web.approve.render] 규율 동형).

세 앱은 별도 서버라 네비 링크는 절대 URL로 서버를 가로지른다. 기본값이
로컬 포트라 데모는 env 없이 동작한다.
"""

import os
from html import escape


def _nav() -> tuple[tuple[str, str, str, str], ...]:
    """(key, 아이콘, 라벨, URL). **렌더 시점**에 env를 읽는다 — 진입 스크립트들은
    dotenv를 main()에서야 로드하므로 import 시점 상수로 두면 .env가 무시된다."""
    chat = os.environ.get("CHAT_WEB_BASE", "http://127.0.0.1:8003").rstrip("/")
    approve = os.environ.get("APPROVE_WEB_BASE", "http://127.0.0.1:8001").rstrip("/")
    onboard = os.environ.get("ONBOARD_WEB_BASE", "http://127.0.0.1:8002").rstrip("/")
    return (
        ("compose", "✏️", "새 포스트", f"{onboard}/compose"),
        ("chat", "💬", "AI 어시스턴트", f"{chat}/"),
        ("queue", "🗂️", "대기열", f"{approve}/"),
        ("channels", "📺", "채널", f"{onboard}/channels"),
    )


BASE_CSS = """
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css");
:root{--primary:#3B5BDB;--primary-light:#EEF2FF;--text:#111827;--muted:#6B7280;
  --border:#E5E7EB;--bg:#FFF;--bg-gray:#F9FAFB;--radius:8px}
*{box-sizing:border-box}
body{margin:0;font-family:"Pretendard",-apple-system,"Malgun Gothic",sans-serif;
  color:var(--text);line-height:1.6;background:var(--bg);
  -webkit-font-smoothing:antialiased}
a{color:var(--primary);text-decoration:none}
a:hover{text-decoration:underline}
.shell{display:flex;min-height:100vh}
.sidebar{width:220px;flex-shrink:0;padding:24px 12px;border-right:1px solid var(--border);
  display:flex;flex-direction:column;gap:4px}
.brand{font-size:17px;font-weight:800;padding:0 14px 16px;letter-spacing:-.5px}
.brand .dot{color:var(--primary)}
.nav-item{display:flex;align-items:center;gap:10px;padding:10px 14px;
  border-radius:var(--radius);font-size:14px;font-weight:600;color:var(--text)}
.nav-item:hover{background:var(--bg-gray);text-decoration:none}
.nav-item.active{color:var(--primary);background:var(--primary-light)}
.main{flex:1;min-width:0;padding:32px}
.content{margin:0 auto}
body.bare{background:var(--bg-gray)}
body.bare .content{margin:40px auto;padding:0 16px}
h1{font-size:24px;font-weight:800;margin:0}
.page-sub{color:var(--muted);font-size:14px;margin:4px 0 28px}
h2{font-size:15px;font-weight:700;margin:24px 0 10px}
h2 .count{color:var(--muted);font-weight:500}
.card{background:#fff;border:1px solid var(--border);border-radius:16px;
  padding:24px;margin-bottom:16px}
.card-list{background:#fff;border:1px solid var(--border);border-radius:16px;
  padding:4px 20px;margin-bottom:16px}
.card-list .row{display:flex;align-items:center;gap:12px;padding:14px 0;
  border-top:1px solid var(--border)}
.card-list .row:first-child{border-top:none}
.row-main{flex:1;min-width:0}
.row-title{font-size:14px;font-weight:600;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.empty{color:var(--muted);text-align:center;padding:48px 20px}
button{padding:9px 16px;background:var(--primary);color:#fff;border:none;
  border-radius:var(--radius);font-size:14px;font-weight:700;cursor:pointer;
  font-family:inherit}
button:hover{background:#2F4AC7}
button.secondary{background:var(--bg-gray);color:var(--text);font-weight:600}
button.secondary:hover{background:var(--border)}
button.danger{background:#FEF2F2;color:#DC2626}
button.danger:hover{background:#FEE2E2}
.textbtn{display:inline-block;font-size:13px;color:var(--muted);font-weight:600;
  margin-bottom:12px}
label{display:block;font-size:13px;font-weight:600;margin:12px 0 6px}
input[type=text],textarea,select{width:100%;padding:10px 14px;
  border:1px solid var(--border);border-radius:var(--radius);font-size:15px;
  font-family:inherit;outline:none}
textarea{resize:vertical;min-height:110px}
:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
form{margin:0}
.actions{margin-top:14px;display:flex;gap:8px}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;
  font-weight:700;white-space:nowrap;flex-shrink:0}
.badge.ok{color:#16A34A;background:#F0FDF4}
.badge.fail{color:#DC2626;background:#FEF2F2}
.badge.busy{color:#D97706;background:#FFFBEB}
.badge.queued{color:#3B5BDB;background:#EEF2FF}
.badge.review{color:#7C3AED;background:#F5F3FF}
.badge.neutral{color:#6B7280;background:#F9FAFB}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 20px}
.chips a{display:inline-block;padding:6px 14px;border:1px solid var(--border);
  border-radius:999px;font-size:13px;font-weight:600;color:var(--text);background:#fff}
.chips a:hover{border-color:var(--primary);text-decoration:none}
.chips a.on{border-color:var(--primary);background:var(--primary-light);
  color:var(--primary)}
.error{padding:12px 16px;border-radius:var(--radius);margin-bottom:16px;
  font-size:14px;font-weight:600;color:#DC2626;background:#FEF2F2}
.notice{padding:12px 16px;border-radius:var(--radius);margin-bottom:16px;
  font-size:14px;font-weight:600;color:#16A34A;background:#F0FDF4}
.meta{color:var(--muted);font-size:13px}
@media(max-width:720px){
  .shell{flex-direction:column}
  .sidebar{width:100%;flex-direction:row;flex-wrap:wrap;padding:12px;
    border-right:none;border-bottom:1px solid var(--border)}
  .brand{padding:0 8px;align-self:center}
  .main{padding:20px 16px}
}
"""

_BRAND = '<div class="brand">multiagent<span class="dot">-sns</span></div>'


def _sidebar(active: str) -> str:
    items = "".join(
        f'<a class="nav-item{" active" if key == active else ""}" href="{url}">'
        f'<span aria-hidden="true">{icon}</span>{label}</a>'
        for key, icon, label, url in _nav()
    )
    return f'<aside class="sidebar">{_BRAND}{items}</aside>'


def page(
    title: str,
    body: str,
    *,
    active: str | None,
    extra_css: str = "",
    max_width: str = "760px",
) -> str:
    """공용 페이지 골격. active=None이면 사이드바 없는 중앙 단독 화면(bare) —
    온보딩 위저드처럼 hidden input으로 상태를 실어 나르는 화면에서 사이드바는
    곧 진행 유실 버튼이라 숨긴다."""
    content = f'<div class="content" style="max-width:{max_width}">{body}</div>'
    if active is None:
        shell = content
        body_cls = ' class="bare"'
    else:
        shell = f'<div class="shell">{_sidebar(active)}<main class="main">{content}</main></div>'
        body_cls = ""
    css = BASE_CSS + extra_css
    return (
        f"<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{css}</style></head>"
        f"<body{body_cls}>{shell}</body></html>"
    )
