"""승인 화면 HTML 렌더 — 순수 함수, 프레임워크 무관 (템플릿 엔진·빌드 단계 없음).

`escape`로 사용자·LLM 산출 텍스트(주제·본문)를 이스케이프해 반영형 XSS를 막는다.
레이아웃·토큰은 [sns.web.layout]의 feedr 공용 골격을 쓰고, 이 파일에는
승인 화면 전용 스타일만 남긴다.
"""

import os
from collections.abc import Mapping
from html import escape
from urllib.parse import quote

from sns.web.approve.store import PendingItem
from sns.web.layout import page

# 통합 서버(scripts/run_web.py)가 이 앱을 /queue에 마운트할 때 내부 링크·리다이렉트
# 앞에 붙는 프리픽스. 단독 실행(:8001)에서는 빈 문자열 — 링크가 그대로다.
URL_PREFIX = os.environ.get("APPROVE_URL_PREFIX", "").rstrip("/")

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


def render_list(items: tuple[PendingItem, ...], *, selected: str = "") -> str:
    """승인 대기 목록 — **채널별로 분류**해 보여준다. `selected`(handle)가 오면 그
    채널만, 기본은 채널별 섹션으로 그룹. 칩이 채널 필터 링크다."""
    handles: list[str] = []
    for item in items:
        if item.handle not in handles:
            handles.append(item.handle)

    chips = ""
    if handles:
        links = [
            f'<a class="{"on" if not selected else ""}" href="{URL_PREFIX}/">전체</a>'
        ] + [
            f'<a class="{"on" if selected == h else ""}" '
            f'href="{URL_PREFIX}/?channel={quote(h)}">{escape(h)}</a>'
            for h in handles
        ]
        chips = f'<div class="chips">{"".join(links)}</div>'

    if not items:
        sections = '<div class="card"><p class="empty">승인 대기 중인 항목이 없습니다.</p></div>'
    elif selected:
        picked = [i for i in items if i.handle == selected]
        rows = (
            f'<div class="card-list">{"".join(_list_row(i) for i in picked)}</div>'
            if picked
            else '<div class="card"><p class="empty">이 채널에는 승인 대기 항목이 없습니다.</p></div>'
        )
        sections = (
            f'<h2>{escape(selected)} <span class="count">{len(picked)}</span></h2>{rows}'
        )
    else:
        parts = []
        for handle in handles:
            group = [i for i in items if i.handle == handle]
            parts.append(
                f'<h2>{escape(handle)} <span class="count">{len(group)}</span></h2>'
                f'<div class="card-list">{"".join(_list_row(i) for i in group)}</div>'
            )
        sections = "".join(parts)

    body = (
        "<h1>대기열</h1>"
        '<p class="page-sub">생성된 초안을 확인하고 승인하면 발행됩니다</p>'
        f"{chips}{sections}"
    )
    return _page("hybrid 승인 대기", body)


def _list_row(item: PendingItem) -> str:
    preview = item.body[:_BODY_PREVIEW_CHARS]
    ellipsis = "…" if len(item.body) > _BODY_PREVIEW_CHARS else ""
    return (
        '<div class="row"><div class="row-main">'
        f'<div class="row-title"><a href="{URL_PREFIX}/items/{item.content_item_id}">'
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
        f'<form method="post" action="{URL_PREFIX}/items/{item.content_item_id}/rerender">'
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
    src = f"{URL_PREFIX}/items/{item.content_item_id}/media"
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
        f'<a class="textbtn" href="{URL_PREFIX}/">← 목록</a>'
        f"<h1>{escape(item.topic_title)}</h1>"
        f'<p class="page-sub">{escape(item.platform)} · {escape(item.handle)} · '
        f"{escape(item.content_format)}{hook}</p>"
        + (f'<div class="notice">{escape(notice)}</div>' if notice else "")
        + (f'<div class="error">{escape(error)}</div>' if error else "")
        + (_video_form(item) if show_video_form else "")
        + '<div class="card">'
        + _media_preview(item)
        + f'<form method="post" action="{URL_PREFIX}/items/{item.content_item_id}/approve">'
        f'<label for="body">본문 (수정 가능)</label>'
        f'<textarea id="body" name="body">{escape(item.body)}</textarea>'
        f'<div class="actions"><button class="approve" type="submit">승인 (수정 반영)</button>'
        "</div></form></div>"
        '<div class="card">'
        f'<form method="post" action="{URL_PREFIX}/items/{item.content_item_id}/reject">'
        f'<label for="reason">반려</label>'
        f'<input type="text" id="reason" name="reason" placeholder="반려 사유(선택)">'
        f'<div class="actions"><button class="reject" type="submit">반려</button></div>'
        "</form></div>"
    )
    return _page(f"승인 — {item.topic_title}", body, max_width="680px")


def render_not_found() -> str:
    body = (
        '<div class="card"><p class="empty">대상을 찾을 수 없습니다(이미 처리됨).</p>'
        f'<p style="text-align:center"><a href="{URL_PREFIX}/">← 목록</a></p></div>'
    )
    return _page("대상 없음", body)
