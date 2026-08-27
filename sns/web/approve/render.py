"""승인 화면 HTML 렌더 — 순수 함수, 프레임워크 무관 (템플릿 엔진·빌드 단계 없음).

`escape`로 사용자·LLM 산출 텍스트(주제·본문)를 이스케이프해 반영형 XSS를 막는다.
레이아웃·토큰은 [sns.web.layout]의 feedr 공용 골격을 쓰고, 이 파일에는
승인 화면 전용 스타일만 남긴다.
"""

from collections.abc import Mapping
from html import escape

from sns.web.approve.store import PendingItem
from sns.web.layout import page

_BODY_PREVIEW_CHARS = 200

_EXTRA_CSS = """
button.reject{background:#FEF2F2;color:#DC2626}
button.reject:hover{background:#FEE2E2}
button.rerender{background:var(--bg-gray);color:var(--text);font-weight:600}
button.rerender:hover{background:var(--border)}
.cut{background:var(--bg-gray);border-radius:var(--radius);padding:14px 16px;
  margin-bottom:12px}
.cut label{color:var(--muted);margin:8px 0 4px}
textarea{min-height:10rem}
video,img.preview{max-width:280px;border-radius:var(--radius);display:block;
  margin:0 0 16px}
"""


def _page(title: str, body: str, *, max_width: str = "760px") -> str:
    return page(title, body, active="queue", extra_css=_EXTRA_CSS, max_width=max_width)


def render_list(items: tuple[PendingItem, ...]) -> str:
    if not items:
        rows = '<div class="card"><p class="empty">승인 대기 중인 항목이 없습니다.</p></div>'
    else:
        rows = f'<div class="card-list">{"".join(_list_row(i) for i in items)}</div>'
    body = (
        "<h1>대기열</h1>"
        '<p class="page-sub">생성된 초안을 확인하고 승인하면 발행됩니다</p>'
        f'<h2>승인 대기 <span class="count">{len(items)}</span></h2>{rows}'
    )
    return _page("hybrid 승인 대기", body)


def _list_row(item: PendingItem) -> str:
    preview = item.body[:_BODY_PREVIEW_CHARS]
    ellipsis = "…" if len(item.body) > _BODY_PREVIEW_CHARS else ""
    return (
        '<div class="row"><div class="row-main">'
        f'<div class="row-title"><a href="/items/{item.content_item_id}">'
        f"{escape(item.topic_title)}</a></div>"
        f'<div class="meta">{escape(item.platform)} · {escape(item.handle)} · '
        f"{escape(item.content_format)} — {escape(preview)}{ellipsis}</div>"
        f'</div><span class="badge review">승인 대기</span></div>'
    )


def _video_form(item: PendingItem) -> str:
    """컷별 자막·나레이션 편집 폼 — 영상 항목 + rerender 배선 시에만 노출."""
    spec = item.media_spec
    assert spec is not None
    slides = spec.get("slides")
    cuts = []
    for i, slide in enumerate(slides if isinstance(slides, list) else []):
        s: Mapping[str, object] = slide if isinstance(slide, Mapping) else {}
        mark = " · 코드 컷" if str(s.get("code", "")).strip() else ""
        cuts.append(
            f'<div class="cut"><strong>컷 {i + 1}{mark}</strong>'
            f'<label for="subtitle_{i}">부제 (한글 최대 20자)</label>'
            f'<input type="text" id="subtitle_{i}" name="subtitle_{i}" '
            f'value="{escape(str(s.get("subtitle", "")))}">'
            f'<label for="narration_{i}">나레이션(=자막, TTS로 읽힌다 · 한글 최대 31자)</label>'
            f'<input type="text" id="narration_{i}" name="narration_{i}" '
            f'value="{escape(str(s.get("narration", "")))}">'
            "</div>"
        )
    quality = (
        f'<div class="meta">품질: {escape(item.quality_status)}</div>'
        if item.quality_status
        else ""
    )
    return (
        f"<div class='card'><h2>영상 내용 수정</h2>{quality}"
        f'<form method="post" action="/items/{item.content_item_id}/rerender">'
        f'<label for="topic">주제(영상 내내 고정)</label>'
        f'<input type="text" id="topic" name="topic" value="{escape(str(spec.get("topic", "")))}">'
        f"{''.join(cuts)}"
        '<div class="actions"><button class="rerender" type="submit">'
        "저장 후 재렌더 (TTS 포함, 수십 초)</button></div></form></div>"
    )


def _media_preview(item: PendingItem) -> str:
    """검수 대상 미리보기 — 경로 텍스트가 아니라 실제 미디어를 보여준다."""
    if item.media_storage_url is None:
        return ""
    src = f"/items/{item.content_item_id}/media"
    if item.media_kind == "video":
        return f'<video controls preload="metadata" src="{src}"></video>'
    return f'<img class="preview" src="{src}" alt="검수 대상 이미지">'


def render_detail(
    item: PendingItem,
    *,
    rerender_enabled: bool = False,
    error: str | None = None,
    notice: str | None = None,
) -> str:
    hook = f" · hook={escape(item.hook_pattern)}" if item.hook_pattern else ""
    show_video_form = (
        rerender_enabled and item.media_kind == "video" and item.media_spec is not None
    )
    body = (
        '<a class="textbtn" href="/">← 목록</a>'
        f"<h1>{escape(item.topic_title)}</h1>"
        f'<p class="page-sub">{escape(item.platform)} · {escape(item.handle)} · '
        f"{escape(item.content_format)}{hook}</p>"
        + (f'<div class="notice">{escape(notice)}</div>' if notice else "")
        + (f'<div class="error">{escape(error)}</div>' if error else "")
        + (_video_form(item) if show_video_form else "")
        + '<div class="card">'
        + _media_preview(item)
        + f'<form method="post" action="/items/{item.content_item_id}/approve">'
        f'<label for="body">본문 (수정 가능)</label>'
        f'<textarea id="body" name="body">{escape(item.body)}</textarea>'
        f'<div class="actions"><button class="approve" type="submit">승인 (수정 반영)</button>'
        "</div></form></div>"
        '<div class="card">'
        f'<form method="post" action="/items/{item.content_item_id}/reject">'
        f'<label for="reason">반려</label>'
        f'<input type="text" id="reason" name="reason" placeholder="반려 사유(선택)">'
        f'<div class="actions"><button class="reject" type="submit">반려</button></div>'
        "</form></div>"
    )
    return _page(f"승인 — {item.topic_title}", body, max_width="680px")


def render_not_found() -> str:
    body = (
        '<div class="card"><p class="empty">대상을 찾을 수 없습니다(이미 처리됨).</p>'
        '<p style="text-align:center"><a href="/">← 목록</a></p></div>'
    )
    return _page("대상 없음", body)
