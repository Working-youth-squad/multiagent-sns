"""키워드 챗봇 화면 HTML 렌더 — 순수 함수 ([sns.web.approve.render] 규율 동형).

템플릿 엔진·빌드 단계·정적 에셋 파이프라인 없음, JS 없음. 폼 POST 후 서버가 대화
전량을 DB에서 다시 읽어 그린다 — 새 답변으로 스크롤을 내리는 건 앵커(`#bottom`)가 한다.

**이 파일이 지키는 규율**은 `scripts/rank_keywords.py:render()`에서 그대로 옮겨 온 것이다.
CLI가 이미 지키고 있고 화면에서 깨뜨리기 쉬운 순서로:

1. `rank_std=None`을 `0.0`으로 표시하지 않는다 → "미정의". "불일치가 없다"와 "불일치를
   잴 수 없다"는 다른 사실이다.
2. `filter_mode` 3값을 뭉뚱그리지 않는다 — 뭉개면 **필터 없는 척**이 된다.
3. `unscored`는 `candidates`의 **부분집합**이다 — 개수를 더해 보여주면 실제 후보 수를 넘는다.
4. 소스 실패를 숨기지 않는다.
5. "밴드를 꺼 보라"는 힌트는 `filter_mode == "active"`일 때만 — 밴드가 자른 게 아닌데
   그렇게 권하면 필터 탓으로 오인하게 만든다.

랭킹 dict는 `sns.chat.agent.ranking_payload`(= `ranking_to_dict`) 산출 그대로다. DB를
왕복해 온 것이든 방금 만든 것이든 같은 모양이라 화면이 갈리지 않는다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape

from sns.chat.drafts import SEED_DONE, ExportItem
from sns.chat.store import ChatMessage, Conversation
from sns.web.layout import page


@dataclass(frozen=True)
class ChatChannel:
    """채널 선택 UI에 필요한 만큼만 — 배선(run_chat_web)이 프로필에서 조립한다."""

    channel_id: str
    label: str
    suggestions: tuple[str, ...] = ()  # 이 채널과 어울리는 대화 주제 제안(프로필 기반)

# 챗봇 전용 스타일 — 골격·토큰은 [sns.web.layout] 공용. 클래스명은 테스트와
# 랭킹/초안 규율이 물고 있으므로 유지하고 색·모양만 feedr 토큰으로 바꾼다.
_EXTRA_CSS = """
.main{padding-bottom:110px}
.turn{margin-bottom:14px;display:flex}
.turn.user{justify-content:flex-end}
.bubble{max-width:80%;padding:10px 16px;border-radius:16px;white-space:pre-wrap;
  word-break:break-word;font-size:15px}
.user .bubble{background:var(--primary);color:#fff;border-bottom-right-radius:4px}
.assistant .bubble{background:var(--bg-gray);border:1px solid var(--border);
  border-bottom-left-radius:4px}
.system .bubble{background:#FFFBEB;color:#92400E;font-size:13px;max-width:100%}
.ranking{background:#fff;border:1px solid var(--border);border-radius:16px;
  padding:20px;margin-bottom:16px}
.ranking h3{margin:0 0 6px;font-size:15px;font-weight:700}
.mode{font-size:13px;font-weight:600;margin:0 0 2px}
.mode.active{color:#16A34A}
.mode.passthrough{color:#D97706}
.mode.off{color:var(--muted)}
.reason{color:var(--muted);font-size:12px;margin:0 0 10px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-weight:700;font-size:12px}
td.num{text-align:right;font-variant-numeric:tabular-nums}
td.undef{text-align:right;color:var(--muted);font-style:italic}
.note{color:var(--muted);font-size:12px;margin:8px 0 0}
.fail{color:#DC2626;font-weight:600}
.composer{position:fixed;bottom:0;left:220px;right:0;background:#fff;
  border-top:1px solid var(--border);padding:14px 16px}
.composer form{max-width:760px;margin:0 auto;display:flex;gap:8px}
.composer input[type=text]{flex:1;width:auto}
.back{display:inline-block;font-size:13px;color:var(--muted);font-weight:600;
  margin-bottom:12px}
.chip{background:#fff;color:var(--text);border:1px solid var(--border);
  padding:6px 14px;font-size:13px;font-weight:600;margin:2px 6px 2px 0;
  border-radius:999px;cursor:pointer}
.chip:hover{border-color:var(--primary);background:var(--primary-light)}
.draft{background:#fff;border:1px solid var(--border);border-radius:16px;
  padding:20px;margin-bottom:16px}
.draft h3{margin:0 0 2px;font-size:15px;font-weight:700}
.draft .meta{margin-bottom:10px}
.draft .card{display:flex;gap:14px;align-items:flex-start;background:transparent;
  border:none;border-radius:0;padding:12px 0 0;margin:12px 0 0;
  border-top:1px solid var(--border)}
.draft .card:first-of-type{border-top:none;padding-top:0;margin-top:0}
.card img,.card video{width:150px;height:auto;max-height:230px;object-fit:contain;
  border-radius:var(--radius);border:1px solid var(--border);flex-shrink:0;
  background:var(--bg-gray)}
.card .who{font-size:13px;color:var(--muted);margin:0 0 4px}
.card .preview{white-space:pre-wrap;word-break:break-word;font-size:14px;margin:0}
.card .rest{color:var(--muted);font-size:12px}
.badge{margin-left:6px;color:var(--muted);background:var(--bg-gray)}
.badge.needs_review{color:#7C3AED;background:#F5F3FF}
.badge.passed{color:#16A34A;background:#F0FDF4}
.badge.blocked{color:#DC2626;background:#FEF2F2}
.approve{display:inline-block;margin-top:6px;font-size:13px}
.actions a{font-size:13px}
.export-grid{display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap}
.export-grid img,.export-grid video{width:270px;height:auto;
  border:1px solid var(--border);border-radius:var(--radius);background:var(--bg-gray)}
.export-side{flex:1;min-width:260px}
.caption{min-height:22rem;line-height:1.5}
.step{border-left:3px solid var(--primary);padding:2px 0 2px 12px;margin:8px 0;
  font-size:14px}
.warn{background:#FFFBEB;color:#B45309;border-radius:var(--radius);
  padding:12px 16px;font-size:13px;font-weight:600;margin:16px 0}
.noimg{width:150px;height:150px;border-radius:var(--radius);
  border:1px dashed var(--border);flex-shrink:0;display:flex;align-items:center;
  justify-content:center;color:var(--muted);font-size:12px;text-align:center;padding:6px}
@media(max-width:720px){.composer{left:0}}
"""

# filter_mode 3값 → 사람이 읽는 문장. 뭉개지 않으려고 상수로 못박는다(규율 2).
FILTER_MODE_TEXT: dict[str, str] = {
    "active": "3소스 교차 분석으로 걸러낸 결과입니다.",
    "passthrough": "데이터가 적어 필터가 열리지 않았습니다 — 거르지 않고 전부입니다.",
    "off": "필터를 끈 전체 목록입니다.",
}


def _page(title: str, body: str) -> str:
    return page(title, body, active="chat", extra_css=_EXTRA_CSS)


def render_not_found() -> str:
    return _page(
        "대화 없음",
        '<p class="empty">그런 대화가 없습니다.</p><p><a href="/">대화 목록으로</a></p>',
    )


def render_index(
    conversations: Sequence[Conversation],
    *,
    channels: Sequence[ChatChannel] = (),
    selected: str | None = None,
    error: str | None = None,
) -> str:
    """대화 목록 + 새 대화 시작 — **채널을 먼저 고른다.**

    채널 칩(링크)으로 채널을 고르면 그 채널의 주제 제안과 대화 기록만 보인다.
    `selected=None`은 전체 보기(채널 없이 시작하면 모든 hybrid 채널에 시드하는
    기존 동작 그대로)다. 채널 배선이 없으면(`channels=()`) 예전 화면과 같다.
    """
    err = f'<p class="fail">{escape(error)}</p>' if error else ""
    picked = next((c for c in channels if c.channel_id == selected), None)

    chips = ""
    if channels:
        links = [
            f'<a class="{"on" if picked is None else ""}" href="/?channel=all">전체</a>'
        ] + [
            f'<a class="{"on" if picked is c else ""}" '
            f'href="/?channel={escape(c.channel_id, quote=True)}">{escape(c.label)}</a>'
            for c in channels
        ]
        chips = f'<div class="chips">{"".join(links)}</div>'

    hidden = (
        f'<input type="hidden" name="channel_id" '
        f'value="{escape(picked.channel_id, quote=True)}">'
        if picked
        else ""
    )
    # 채널 프로필에서 온 주제 제안 — 누르면 입력란이 채워진다(온보딩 칩과 같은 관용구).
    suggest = ""
    if picked and picked.suggestions:
        chips_html = "".join(
            f'<button type="button" class="chip" '
            f'onclick="this.form.text.value=this.textContent">{escape(s)}</button>'
            for s in picked.suggestions
        )
        suggest = (
            f"<p class='meta'>이 채널과 어울리는 주제 — 눌러서 채우기</p>{chips_html}"
        )
    start = (
        '<div class="card"><form method="post" action="/conversations">'
        f"{hidden}"
        f'<label for="new-text">무엇을 다뤄볼까요?{f" — {escape(picked.label)}" if picked else ""}</label>'
        f"{suggest}"
        '<input type="text" id="new-text" name="text" '
        'placeholder="어떤 주제를 다루고 싶으세요? (예: 개발자 취업)">'
        '<div class="actions"><button type="submit">새 대화 시작</button></div>'
        "</form></div>"
    )

    labels = {c.channel_id: c.label for c in channels}
    shown = [
        c
        for c in conversations
        if picked is None or c.channel_id == picked.channel_id
    ]
    if shown:
        rows = "".join(
            '<div class="row"><div class="row-main">'
            f'<div class="row-title"><a href="/c/{escape(c.conversation_id)}">'
            f"{escape(c.title or '제목 없는 대화')}</a></div>"
            f'<div class="meta">{c.created_at:%Y-%m-%d %H:%M}</div></div>'
            + (
                f'<span class="badge neutral">{escape(labels[c.channel_id])}</span>'
                if c.channel_id and c.channel_id in labels
                else ""
            )
            + "</div>"
            for c in shown
        )
        past = (
            f'<h2>지난 대화 <span class="count">{len(shown)}</span></h2>'
            f'<div class="card-list">{rows}</div>'
        )
    else:
        past = ""
    body = (
        "<h1>AI 어시스턴트</h1>"
        '<p class="page-sub">채널을 고르고, 키워드를 찾고, 대화로 초안까지 만듭니다</p>'
        f"{err}{chips}{start}{past}"
    )
    return _page("키워드 챗봇", body)


def render_conversation(
    conversation: Conversation,
    messages: Sequence[ChatMessage],
    *,
    channel_label: str | None = None,
    error: str | None = None,
) -> str:
    """대화 1건 전체. 매 턴 이 함수가 처음부터 다시 그린다(hidden state 없음)."""
    turns = "".join(_message(m) for m in messages)
    if not turns:
        turns = '<p class="empty">첫 마디를 건네보세요.</p>'
    err = f'<p class="fail">{escape(error)}</p>' if error else ""
    composer = (
        '<div class="composer"><form method="post" '
        f'action="/c/{escape(conversation.conversation_id)}/messages">'
        '<input type="text" name="text" placeholder="메시지" autofocus autocomplete="off">'
        "<button type='submit'>보내기</button></form></div>"
    )
    title = conversation.title or "키워드 챗봇"
    channel = (
        f'<p class="page-sub">채널: <span class="badge neutral">{escape(channel_label)}</span></p>'
        if channel_label
        else ""
    )
    body = (
        '<a class="back" href="/">← 대화 목록</a>'
        f"<h1>{escape(title)}</h1>{channel}{err}{turns}"
        f'<div id="bottom"></div>{composer}'
    )
    return _page(title, body)


def _message(message: ChatMessage) -> str:
    if message.role == "ranking":
        return render_ranking(message.payload or {})
    payload = message.payload or {}
    if message.role == "system" and payload.get("kind") == SEED_DONE:
        return render_drafts(payload)
    return (
        f'<div class="turn {escape(message.role)}">'
        f'<div class="bubble">{escape(message.body)}</div></div>'
    )


def render_ranking(payload: Mapping[str, object]) -> str:
    """랭킹 원본 → 표. **원본만 본다** — LLM이 다시 쓴 문장을 여기서 쓰지 않는다."""
    query = str(payload.get("query", ""))
    mode = str(payload.get("filter_mode", "off"))
    # 모르는 값이 오면 조용히 "off"로 접지 않는다 — 뭉개기의 다른 이름이다(규율 2).
    mode_text = FILTER_MODE_TEXT.get(mode, f"알 수 없는 필터 상태({mode}) — 원본을 확인하세요.")
    mode_class = mode if mode in FILTER_MODE_TEXT else "off"
    reason = str(payload.get("reason", ""))

    candidates = _stats(payload.get("candidates"))
    rows = "".join(_row(i, c) for i, c in enumerate(candidates, 1))
    if rows:
        table = (
            "<table><tr><th>#</th><th>키워드</th><th style='text-align:right'>소스</th>"
            "<th style='text-align:right'>관측 평균등수</th>"
            "<th style='text-align:right'>소스간 불일치</th></tr>"
            f"{rows}</table>"
        )
    else:
        # 규율 5 — 밴드가 자른 게 아닌데 "꺼 보라"고 권하면 필터 탓으로 오인시킨다.
        if mode == "active":
            table = '<p class="note">후보 없음 — 필터를 끄면 전량을 볼 수 있습니다.</p>'
        else:
            table = (
                '<p class="note">후보 없음 — 밴드는 열리지 않았습니다. 소스 응답이 비었습니다.</p>'
            )

    return (
        '<div class="ranking">'
        f"<h3>‘{escape(query)}’ 연관 키워드</h3>"
        f'<p class="mode {escape(mode_class)}">{escape(mode_text)}</p>'
        f'<p class="reason">{escape(reason)}</p>'
        f"{table}{_sources(payload)}{_footnotes(payload, candidates)}"
        "</div>"
    )


def _row(index: int, stat: Mapping[str, object]) -> str:
    text = escape(str(stat.get("text", "")))
    present = escape(str(stat.get("present_count", "")))
    mean = stat.get("observed_mean")
    mean_cell = f"{float(mean):.4f}" if isinstance(mean, int | float) else "—"
    std = stat.get("rank_std")
    if not isinstance(std, int | float):
        # None(잴 수 없었다)과 모양이 깨진 값을 같은 칸으로 접는다 — 어느 쪽이든
        # 수치를 지어낼 근거가 없다는 사실은 같다.
        # 규율 1 — 0.0으로 채우면 "불일치 없음"으로 읽힌다. 잴 수 없었다는 뜻이다.
        std_cell = (
            '<td class="undef" title="관측이 1건이라 소스간 불일치를 잴 수 없습니다">미정의</td>'
        )
    else:
        std_cell = f'<td class="num">{float(std):.4f}</td>'
    return (
        f'<tr><td class="num">{index}</td><td>{text}</td>'
        f'<td class="num">{present}</td><td class="num">{mean_cell}</td>{std_cell}</tr>'
    )


def _sources(payload: Mapping[str, object]) -> str:
    """규율 4 — 소스 실패를 숨기지 않는다."""
    ok = [str(s) for s in _seq(payload.get("sources_ok"))]
    failed = [str(s) for s in _seq(payload.get("sources_failed"))]
    got = escape(", ".join(ok)) if ok else "없음"
    note = f"소스 성공 {got}"
    if failed:
        note += f' · <span class="fail">실패 {escape(", ".join(failed))}</span>'
    return f'<p class="note">{note}</p>'


def _footnotes(payload: Mapping[str, object], candidates: Sequence[Mapping[str, object]]) -> str:
    notes: list[str] = []
    dropped = _stats(payload.get("dropped"))
    if dropped:
        shown = ", ".join(
            f"{s.get('text')}({_std(s):.3f})"
            for s in dropped
            # `if s['rank_std']`로 거르면 하위 꼬리(정확히 0.0)가 통째로 사라진다 — None만 뺀다.
            if isinstance(s.get("rank_std"), int | float)
        )
        notes.append(f"밴드 밖 {len(dropped)}건: {shown}")

    unscored = [str(t) for t in _seq(payload.get("unscored"))]
    if unscored:
        # 규율 3 — "후보 N건 + 미판정 M건"으로 읽히지 않게 부분집합임을 문장에 못박는다.
        #
        # 다만 `unscored`는 `top` 컷 **이전** 전량에서 계산된다(sns.research.keywords.
        # aggregate). 그래서 "후보 N건 중 M건"이라고 쓰면 M > N이 나올 수 있다 — 실제로
        # 후보 10건에 미판정 23건이 나온다. 그 문장 자체가 실제 후보 수를 넘기는,
        # 이 규율이 막으려던 바로 그 모양이다. 표에 보이는 것과 표 밖의 것을 나눠 센다.
        listed = {str(c.get("text", "")) for c in candidates}
        here = [t for t in unscored if t in listed]
        elsewhere = len(unscored) - len(here)
        if here:
            notes.append(
                f"위 후보 {len(candidates)}건 중 {len(here)}건은 관측이 1곳뿐이라"
                f" 불일치를 잴 수 없었습니다(별도 후보가 아닙니다): {', '.join(here)}"
            )
        if elsewhere:
            notes.append(
                f"상한에 걸려 표에 없는 후보 중에도 {elsewhere}건이 같은 이유로 미판정입니다."
            )

    below = _stats(payload.get("below_min_present"))
    if below:
        names = ", ".join(str(s.get("text", "")) for s in below)
        notes.append(f"교차검증 하한 미달 {len(below)}건: {names}")

    excluded = _seq(payload.get("excluded"))
    if excluded:
        hits = ", ".join(
            f"{e.get('text')}←{e.get('keyword')}" for e in excluded if isinstance(e, Mapping)
        )
        notes.append(f"제외 {len(excluded)}건: {hits}")

    return "".join(f'<p class="note">{escape(n)}</p>' for n in notes)


def _std(stat: Mapping[str, object]) -> float:
    value = stat.get("rank_std")
    assert isinstance(value, int | float)  # 호출부가 isinstance로 이미 걸렀다
    return float(value)


def _seq(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, str) else ()


def _stats(value: object) -> list[Mapping[str, object]]:
    return [s for s in _seq(value) if isinstance(s, Mapping)]


def render_drafts(payload: Mapping[str, object]) -> str:
    """시드 사이클 결과 → 초안 카드 ([sns.chat.drafts.seed_done_payload] 산출을 읽는다).

    본문은 미리보기다 — 정본은 `content_item.body`이고 승인 화면이 그것을 편집한다.
    잘렸으면 **잘렸다고 밝힌다**. 요약해 놓고 전문인 척하면 사용자가 승인 화면에서
    처음 보는 문장이 생긴다.
    """
    title = str(payload.get("topic_title", ""))
    items = _stats(payload.get("items"))
    prepared = [i for i in items if i.get("outcome") == "prepared"]

    if prepared:
        head = f"초안 {len(prepared)}건이 만들어졌습니다 — 승인하면 발행됩니다."
    elif items:
        head = "초안이 만들어지지 않았습니다."
    else:
        head = "초안을 만들 대상이 없습니다."

    cards = "".join(_draft_card(i) for i in items)
    if not cards:
        cards = '<p class="note">hybrid 모드 채널이 필요합니다(온보딩 :8002).</p>'

    return (
        '<div class="draft">'
        f"<h3>‘{escape(title)}’</h3>"
        f'<p class="meta">{escape(head)}</p>'
        f"{cards}</div>"
    )


def _media_tag(asset_id: str, media_kind: str) -> str:
    """자산 종류로 <img> ↔ <video>를 가른다 — **종류는 원장이 준다**.

    확장자나 MIME으로 추측하지 않는다. 화면에는 저장소 URL이 오지 않고(`/media/{id}`
    중계) 응답을 미리 받아볼 수도 없다. 종류를 payload에 실어 보내는 이유가 이것이다
    ([sns.chat.drafts.DraftItem.media_kind]).

    `preload="metadata"`인 이유: 대화 한 화면에 초안이 여러 건 붙을 수 있는데 전부
    자동 로드하면 mp4 수 MB가 한꺼번에 흐른다. 첫 프레임만 받아 포스터로 쓴다.
    """
    src = f"/media/{escape(asset_id)}"
    if media_kind == "video":
        return f'<video src="{src}" controls preload="metadata" playsinline></video>'
    return f'<img src="{src}" alt="생성된 카드 이미지">'


def _draft_card(item: Mapping[str, object]) -> str:
    outcome = str(item.get("outcome", ""))
    who = escape(str(item.get("channel_label", "")))

    if outcome != "prepared":
        # 실패·차단도 카드로 남긴다 — 사유가 그 대상의 결과 전부다.
        reason = str(item.get("error") or _OUTCOME_TEXT.get(outcome, outcome))
        return (
            '<div class="card">'
            f'<div class="noimg">{escape(_OUTCOME_TEXT.get(outcome, outcome))}</div>'
            f'<div><p class="who">{who}<span class="badge blocked">'
            f"{escape(outcome)}</span></p>"
            f'<p class="preview">{escape(reason)}</p></div></div>'
        )

    asset_id = item.get("media_asset_id")
    if isinstance(asset_id, str) and asset_id:
        thumb = _media_tag(asset_id, str(item.get("media_kind") or "image"))
    else:
        thumb = '<div class="noimg">자산 없음</div>'

    # 주 뱃지는 **승인 상태**다. 미디어 품질이 passed여도 사람 승인 전이면 나가지 않는다 —
    # 품질만 보여주면 "통과했다"로 읽혀 발행된 줄 안다.
    badge = _badge(str(item.get("content_status") or ""), _CONTENT_TEXT)
    quality = str(item.get("quality_status") or "")
    if quality and quality != "passed":
        # 품질은 문제가 있을 때만 덧붙인다 — 통과는 승인 상태가 이미 말한 것에 더할 게 없다.
        badge += _badge(quality, _QUALITY_TEXT)

    preview = str(item.get("body_preview", ""))
    length = item.get("body_length")
    rest = ""
    if isinstance(length, int) and length > len(preview):
        rest = (
            f'<p class="rest">…이하 {length - len(preview)}자는 승인 화면에서 볼 수 있습니다.</p>'
        )

    url = item.get("approve_url")
    links = []
    if isinstance(url, str) and url:
        links.append(
            f'<a href="{escape(url)}" target="_blank" rel="noopener">승인 화면에서 확인·수정 →</a>'
        )
    item_id = item.get("content_item_id")
    if isinstance(item_id, str) and item_id:
        # 손으로 올리려면 **잘리지 않은** 캡션과 이미지 파일이 필요하다 — 여기 미리보기는
        # 잘려 있으므로 전용 화면으로 보낸다.
        links.append(f'<a href="/export/{escape(item_id)}">수동 발행용 내보내기 →</a>')
    actions = f'<div class="actions">{"".join(links)}</div>' if links else ""

    return (
        '<div class="card">'
        f"{thumb}"
        f'<div><p class="who">{who}{badge}</p>'
        f'<p class="preview">{escape(preview)}</p>{rest}{actions}</div></div>'
    )


_OUTCOME_TEXT: dict[str, str] = {
    "blocked": "게이트 차단",
    "failed": "제작 실패",
    "manual_assigned": "주제만 배정",
}


def _badge(value: str, labels: Mapping[str, str]) -> str:
    if not value:
        return ""
    return f'<span class="badge {escape(value)}">{escape(labels.get(value, value))}</span>'


# content_item.status — 발행을 실제로 막는 값.
_CONTENT_TEXT: dict[str, str] = {
    "needs_review": "승인 대기",
    "draft": "초안",
    "approved": "승인됨",
    "rejected": "반려됨",
}

# media_asset.quality_status — 렌더 산출물 품질. 승인 여부가 아니다.
_QUALITY_TEXT: dict[str, str] = {
    "needs_review": "품질 미판정",
    "failed": "품질 미달",
}


def render_export(item: ExportItem) -> str:
    """수동 발행 내보내기 — 사람이 플랫폼 앱에 손으로 올릴 재료 전부.

    **캡션을 자르지 않는다.** 초안 카드의 미리보기와 정반대의 요구다: 잘린 캡션을
    복사하면 그대로 잘린 채 게시된다. `<textarea>`에 전문을 넣어 전체 선택·복사가
    되게 하고, 파일로도 받을 수 있게 한다(JS 없이).
    """
    stem = item.filename_stem
    cid = escape(item.content_item_id)
    is_video = item.media_kind == "video"
    if item.media_asset_id:
        src = f"/media/{escape(item.media_asset_id)}"
        label = "영상 내려받기" if is_video else "이미지 내려받기"
        image = (
            f"<div>{_media_tag(item.media_asset_id, item.media_kind)}"
            f'<div class="actions">'
            f'<a href="{src}?download=1&amp;name={escape(stem)}" download>{label}</a>'
            "</div></div>"
        )
    else:
        image = '<div class="noimg">렌더 자산이 없습니다.</div>'

    # 승인 전 원고를 손으로 올리면 사람 관문(FR-Q3)을 건너뛴 것이 된다. 막지는 않되
    # 어떤 상태인지는 반드시 알린다 — 모르고 올리는 것과 알고 올리는 것은 다르다.
    if item.content_status != "approved":
        gate = (
            f'<p class="warn">이 원고는 아직 <b>{escape(item.content_status)}</b> 상태입니다 — '
            "승인 화면을 거치지 않은 초안입니다. 그대로 올리면 사람 확인 관문을 건너뜁니다.</p>"
        )
    else:
        gate = ""

    steps = (
        f'<div class="step">1. {"영상" if is_video else "이미지"}을(를) 내려받습니다.</div>'
        '<div class="step">2. 아래 캡션을 전체 선택해 복사하거나 .txt로 내려받습니다.</div>'
        f'<div class="step">3. {escape(item.platform)} 앱에서 직접 올립니다.</div>'
        f'<div class="step">4. {_register_step(item)}</div>'
    )

    body = (
        f'<a class="back" href="javascript:history.back()">← 돌아가기</a>'
        f"<h1>{escape(item.topic_title)}</h1>"
        f'<p class="meta">{escape(item.channel_label)} · 수동 발행용 내보내기</p>'
        f"{gate}"
        f'<div class="export-grid">{image}'
        f'<div class="export-side">{steps}'
        f'<label class="meta" for="caption">캡션 (전문 — 잘리지 않았습니다)</label>'
        f'<textarea class="caption" id="caption" readonly>{escape(item.body)}</textarea>'
        f'<div class="actions">'
        f'<a href="/export/{cid}/caption.txt" download>캡션 .txt 내려받기</a>'
        f'<span class="meta">{len(item.body)}자</span>'
        "</div></div></div>"
        f'<p class="note">원장 등록용 값 — content_item_id: <code>{cid}</code></p>'
    )
    return _page(f"{item.topic_title} — 내보내기", body)


def _register_step(item: ExportItem) -> str:
    """올린 뒤 원장에 등록하는 방법 — **채널 모드에 따라 실제로 되는 것만** 안내한다.

    `sns.publish.manual`은 `manual` 채널만 받는다. hybrid/auto 건을 손으로 올리면 등록
    경로가 없어 `publication`이 pending으로 남는다. 되는 것처럼 안내하면 사용자를 곧장
    실패로 보낸다 — 안 되는 것은 안 된다고 말하는 편이 낫다.
    """
    mode = item.channel_mode
    if mode == "manual":
        return (
            "올린 뒤 게시물 ID를 등록하면 지표 수집이 이어집니다 — "
            "<code>scripts/manual_register.py</code>에 아래 id와 게시물 ID를 넘기세요."
        )
    if mode is None:
        return "이 원고에 연결된 채널을 찾지 못했습니다 — 올린 뒤 원장 등록 경로가 없습니다."
    return (
        f"<b>이 채널은 {escape(mode)} 모드라 손으로 올린 결과를 원장에 등록할 수 없습니다.</b> "
        "수동 등록은 manual 채널 전용입니다(기계 발행 채널에 손으로 등록하면 auto vs hybrid "
        "비교가 오염됩니다). 올려도 발행 기록은 <code>pending</code>으로 남고 지표 수집이 "
        "이어지지 않습니다."
    )
