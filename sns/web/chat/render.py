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
from html import escape

from sns.chat.store import ChatMessage, Conversation

_STYLE = """<style>
body{font-family:system-ui,-apple-system,"Malgun Gothic",sans-serif;max-width:760px;
  margin:2rem auto;padding:0 1rem 6rem;color:#1a1a1a;line-height:1.5}
h1{font-size:1.3rem}
.turn{margin-bottom:1rem;display:flex}
.turn.user{justify-content:flex-end}
.bubble{max-width:80%;padding:.7rem .95rem;border-radius:12px;white-space:pre-wrap;
  word-break:break-word}
.user .bubble{background:#2e7d32;color:#fff;border-bottom-right-radius:3px}
.assistant .bubble{background:#f1f3f1;border-bottom-left-radius:3px}
.system .bubble{background:#fff8e1;border:1px solid #ffe082;color:#5d4037;
  font-size:.88rem;max-width:100%}
.ranking{border:1px solid #ddd;border-radius:10px;padding:.9rem 1rem;margin-bottom:1rem;
  background:#fff}
.ranking h3{margin:0 0 .35rem;font-size:1rem}
.mode{font-size:.85rem;margin:0 0 .2rem}
.mode.active{color:#2e7d32}
.mode.passthrough{color:#ef6c00}
.mode.off{color:#666}
.reason{color:#666;font-size:.8rem;margin:0 0 .6rem}
table{border-collapse:collapse;width:100%;font-size:.88rem}
th,td{text-align:left;padding:.32rem .5rem;border-bottom:1px solid #eee}
th{color:#666;font-weight:600;font-size:.8rem}
td.num{text-align:right;font-variant-numeric:tabular-nums}
td.undef{text-align:right;color:#999;font-style:italic}
.note{color:#666;font-size:.8rem;margin:.55rem 0 0}
.fail{color:#c62828}
.composer{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:1px solid #ddd;
  padding:.7rem 1rem}
.composer form{max-width:760px;margin:0 auto;display:flex;gap:.5rem}
.composer input[type=text]{flex:1;padding:.6rem;border:1px solid #ccc;border-radius:8px;
  font-size:1rem}
button{padding:.6rem 1.2rem;border:none;border-radius:8px;cursor:pointer;font-size:.95rem;
  background:#2e7d32;color:#fff}
.item{border:1px solid #ddd;border-radius:8px;padding:.8rem 1rem;margin-bottom:.7rem}
.item a{color:#1a1a1a;text-decoration:none}
.item a:hover{text-decoration:underline}
.meta{color:#666;font-size:.82rem}
.empty{color:#666;text-align:center;padding:3rem 0}
.back{display:inline-block;margin-bottom:1rem;color:#666}
a{color:#2e7d32}
</style>"""

# filter_mode 3값 → 사람이 읽는 문장. 뭉개지 않으려고 상수로 못박는다(규율 2).
FILTER_MODE_TEXT: dict[str, str] = {
    "active": "3소스 교차 분석으로 걸러낸 결과입니다.",
    "passthrough": "데이터가 적어 필터가 열리지 않았습니다 — 거르지 않고 전부입니다.",
    "off": "필터를 끈 전체 목록입니다.",
}


def _page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title>{_STYLE}</head><body>{body}</body></html>"
    )


def render_not_found() -> str:
    return _page(
        "대화 없음",
        '<p class="empty">그런 대화가 없습니다.</p><p><a href="/">대화 목록으로</a></p>',
    )


def render_index(conversations: Sequence[Conversation], *, error: str | None = None) -> str:
    """대화 목록 + 새 대화 시작."""
    err = f'<p class="fail">{escape(error)}</p>' if error else ""
    start = (
        '<form method="post" action="/conversations">'
        '<input type="text" name="text" '
        'placeholder="어떤 주제를 다루고 싶으세요? (예: 개발자 취업)" '
        "style='width:100%;padding:.6rem;border:1px solid #ccc;border-radius:8px;font-size:1rem'>"
        '<button type="submit" style="margin-top:.6rem">새 대화 시작</button></form>'
    )
    if conversations:
        rows = "".join(
            f'<div class="item"><a href="/c/{escape(c.conversation_id)}">'
            f"{escape(c.title or '제목 없는 대화')}</a>"
            f'<div class="meta">{c.created_at:%Y-%m-%d %H:%M}</div></div>'
            for c in conversations
        )
        past = f"<h2 style='font-size:1rem;margin-top:2rem'>지난 대화</h2>{rows}"
    else:
        past = ""
    return _page("키워드 챗봇", f"<h1>키워드 챗봇</h1>{err}{start}{past}")


def render_conversation(
    conversation: Conversation,
    messages: Sequence[ChatMessage],
    *,
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
    body = (
        '<a class="back" href="/">← 대화 목록</a>'
        f"<h1>{escape(title)}</h1>{err}{turns}"
        f'<div id="bottom"></div>{composer}'
    )
    return _page(title, body)


def _message(message: ChatMessage) -> str:
    if message.role == "ranking":
        return render_ranking(message.payload or {})
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
