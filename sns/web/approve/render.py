"""승인 화면 HTML 렌더 — 순수 함수, 프레임워크 무관 (템플릿 엔진·빌드 단계 없음).

`escape`로 사용자·LLM 산출 텍스트(주제·본문)를 이스케이프해 반영형 XSS를 막는다.
별도 정적 에셋 파이프라인 없이 로컬에서 바로 뜨는 것을 우선한다(팀 스택 "경량
프론트", 02-아키텍처-스택 §웹).
"""

from collections.abc import Mapping
from html import escape

from sns.web.approve.store import PendingItem

_BODY_PREVIEW_CHARS = 200

_STYLE = """<style>
body{font-family:system-ui,-apple-system,"Malgun Gothic",sans-serif;max-width:720px;
  margin:2rem auto;padding:0 1rem;color:#1a1a1a;line-height:1.5}
.item{border:1px solid #ddd;border-radius:8px;padding:1rem;margin-bottom:1rem}
.item h3{margin:0 0 .25rem;font-size:1.05rem}
.item a{color:#1565c0;text-decoration:underline}
.item a:hover{color:#0d47a1}
.meta{color:#666;font-size:.85rem;margin-bottom:.5rem}
textarea{width:100%;min-height:10rem;font-family:inherit;font-size:1rem;padding:.6rem;
  box-sizing:border-box;border:1px solid #ccc;border-radius:6px}
input[type=text]{width:100%;padding:.5rem;box-sizing:border-box;border:1px solid #ccc;
  border-radius:6px;font-size:.95rem}
form{margin-top:.75rem}
.actions{margin-top:.5rem;display:flex;gap:.5rem}
button{padding:.55rem 1.1rem;border:none;border-radius:6px;cursor:pointer;font-size:.95rem}
.approve{background:#2e7d32;color:#fff}
.reject{background:#c62828;color:#fff}
.empty{color:#666;text-align:center;padding:3rem 0}
.back{display:inline-block;margin-bottom:1rem;color:#1565c0;font-weight:600}
.cut{border:1px solid #ddd;border-radius:8px;padding:.75rem;margin-bottom:.75rem}
.cut label{display:block;font-size:.8rem;color:#666;margin:.4rem 0 .15rem}
.rerender{background:#1565c0;color:#fff}
.error{background:#fdecea;border:1px solid #c62828;color:#c62828;border-radius:6px;
  padding:.6rem .8rem;margin-bottom:1rem}
.notice{background:#e8f5e9;border:1px solid #2e7d32;color:#2e7d32;border-radius:6px;
  padding:.6rem .8rem;margin-bottom:1rem}
video,img.preview{max-width:280px;border-radius:8px;display:block;margin:.5rem 0 1rem}
</style>"""


def _page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{escape(title)}</title>{_STYLE}</head><body>{body}</body></html>"
    )


def render_list(items: tuple[PendingItem, ...]) -> str:
    if not items:
        rows = '<p class="empty">승인 대기 중인 항목이 없습니다.</p>'
    else:
        rows = "".join(_list_row(i) for i in items)
    return _page("hybrid 승인 대기", f"<h1>승인 대기 ({len(items)}건)</h1>{rows}")


def _list_row(item: PendingItem) -> str:
    preview = item.body[:_BODY_PREVIEW_CHARS]
    ellipsis = "…" if len(item.body) > _BODY_PREVIEW_CHARS else ""
    return (
        f'<div class="item">'
        f'<h3><a href="/items/{item.content_item_id}">{escape(item.topic_title)}</a></h3>'
        f'<div class="meta">{escape(item.platform)} · {escape(item.handle)} · '
        f"{escape(item.content_format)}</div>"
        f"<p>{escape(preview)}{ellipsis}</p></div>"
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
        f"<h2>영상 내용 수정</h2>{quality}"
        f'<form method="post" action="/items/{item.content_item_id}/rerender">'
        f'<label for="topic">주제(영상 내내 고정)</label>'
        f'<input type="text" id="topic" name="topic" value="{escape(str(spec.get("topic", "")))}">'
        f"{''.join(cuts)}"
        '<div class="actions"><button class="rerender" type="submit">'
        "저장 후 재렌더 (TTS 포함, 수십 초)</button></div></form>"
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
        '<a class="back" href="/">← 목록</a>'
        f"<h1>{escape(item.topic_title)}</h1>"
        f'<div class="meta">{escape(item.platform)} · {escape(item.handle)} · '
        f"{escape(item.content_format)}{hook}</div>"
        + (f'<div class="notice">{escape(notice)}</div>' if notice else "")
        + (f'<div class="error">{escape(error)}</div>' if error else "")
        + _media_preview(item)
        + (_video_form(item) if show_video_form else "")
        + f'<form method="post" action="/items/{item.content_item_id}/approve">'
        f'<textarea name="body">{escape(item.body)}</textarea>'
        f'<div class="actions"><button class="approve" type="submit">승인 (수정 반영)</button>'
        "</div></form>"
        f'<form method="post" action="/items/{item.content_item_id}/reject">'
        f'<input type="text" name="reason" placeholder="반려 사유(선택)">'
        f'<div class="actions"><button class="reject" type="submit">반려</button></div>'
        "</form>"
    )
    return _page(f"승인 — {item.topic_title}", body)


def render_not_found() -> str:
    body = (
        '<p class="empty">대상을 찾을 수 없습니다(이미 처리됨).</p>'
        '<p><a class="back" href="/">← 목록</a></p>'
    )
    return _page("대상 없음", body)
