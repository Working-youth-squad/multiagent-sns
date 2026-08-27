"""온보딩 인터뷰 화면 HTML 렌더 — 순수 함수 ([sns.web.approve.render] 규율 동형).

플로우: 시작 분기(기존 계정 연동 vs 새로 만들기) → 인터뷰(1~5) → 컨셉 확정
화면에서 **그때** 계정을 등록한다 — 채널 없이 인터뷰가 먼저다. 연동 경로는
계정 정보(platform·handle)를 state에 실어 나를 뿐 등록 시점은 같다.
위저드는 stateless: 각 화면 폼이 이전 답을 hidden input으로
다음 화면에 넘긴다(세션 스토어 없음). 화면당 결정 1개 + 진행바(n/5).
`escape`로 사용자 입력을 이스케이프해 반영형 XSS를 막는다.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape

from sns.goals import GOAL_PRESETS
from sns.onboarding.profile import (
    CHARACTER_STYLES,
    MAX_TOPIC_SUBS,
    TONES,
    TOPIC_MAJORS,
    ChannelProfile,
)
from sns.onboarding.store import ChannelRow
from sns.web.layout import page

TOTAL_STEPS = 5
SUBS_SEP = "|"  # hidden input에서 세부 주제를 잇는 구분자(쉼표는 직접 입력에 쓰인다)

_PLATFORM_ICON = {"youtube": "▶️", "instagram": "📷"}


def _channel_head(channel: ChannelRow, active: str, *, video_enabled: bool) -> str:
    """채널 하위 화면 공통 헤더 + [프로필|영상] 탭. 영상 탭은 배선이 있을 때만."""
    tabs = [
        f'<a class="tab{" active" if active == "profile" else ""}" '
        f'href="/channels/{channel.channel_id}">프로필</a>'
    ]
    if video_enabled:
        tabs.append(
            f'<a class="tab{" active" if active == "videos" else ""}" '
            f'href="/channels/{channel.channel_id}/videos">영상</a>'
        )
    return (
        f"<h1>{_PLATFORM_ICON.get(channel.platform, '📺')} {escape(channel.handle)}</h1>"
        f'<p class="page-sub">{escape(channel.platform)} · '
        f'<span class="badge neutral">{escape(channel.mode)}</span></p>'
        f'<div class="tabs">{"".join(tabs)}</div>'
    )


@dataclass(frozen=True)
class VideoItemView:
    """영상 관리 탭의 항목 1건 — 대본(script)에서 시작해 렌더를 거쳐 영상(done)이 된다."""

    item_id: str
    topic: str
    state: str  # "script"(대본 승인 대기) | "rendering" | "done" | "failed"
    slides: tuple[tuple[str, str], ...] = ()  # (부제, 나레이션) — 대본 표시용
    body: str = ""  # 캡션(발행 본문)
    video_path: str = ""  # done일 때 mp4 로컬 경로 — 앱이 media 라우트로 서빙
    log_tail: str = ""  # failed일 때 원인


@dataclass(frozen=True)
class ScriptJobView:
    """새 대본 생성 작업(사이클) 상태 — 아직 DB 항목이 없는 구간의 표시용."""

    state: str  # "running" | "failed"
    log_tail: str = ""


# 온보딩 전용 스타일 — 골격·토큰은 [sns.web.layout] 공용. 위저드는 bare(사이드바
# 없음) 중앙 화면이다: hidden input으로 상태를 나르는 화면에서 사이드바는 곧
# 진행 유실 버튼이라 숨긴다.
_EXTRA_CSS = """
.progress{height:6px;background:var(--border);border-radius:3px;margin-bottom:24px}
.progress>div{height:100%;background:var(--primary);border-radius:3px}
.step-label{color:var(--muted);font-size:13px;font-weight:600;margin-bottom:4px}
h1{margin-bottom:16px}
.choice{display:block;background:#fff;border:1px solid var(--border);
  border-radius:var(--radius);padding:12px 16px;margin-bottom:8px;cursor:pointer;
  font-size:15px}
.choice:hover{border-color:var(--primary)}
.choice:has(input:checked){border-color:var(--primary);background:var(--primary-light)}
.choice input{margin-right:10px}
.choice .desc{color:var(--muted);font-size:13px;margin:2px 0 0 26px}
form button{margin-top:12px}
.chip{background:#fff;color:var(--text);border:1px solid var(--border);
  padding:6px 14px;font-size:13px;font-weight:600;margin:2px 6px 2px 0;
  border-radius:999px}
.chip:hover{border-color:var(--primary);background:var(--primary-light)}
.card h3{margin:0 0 8px;font-size:15px;font-weight:700}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}
.grid .card{margin-bottom:0}
.chip-row{display:flex;flex-wrap:wrap;gap:8px}
.chip-radio{display:inline-flex;align-items:center;gap:8px;padding:8px 14px;
  border-radius:999px;font-size:14px;font-weight:600;border:1px solid var(--border);
  background:#fff;cursor:pointer}
.chip-radio:has(input:checked){border-color:var(--primary);
  background:var(--primary-light)}
.chip-radio input{margin:0}
button.big{padding:12px 28px;font-size:15px}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--border);margin:16px 0 20px}
.tab{display:inline-block;padding:8px 14px;font-size:14px;font-weight:600;
  color:var(--muted)}
.tab:hover{color:var(--text);text-decoration:none}
.tab.active{color:var(--primary);border-bottom:2px solid var(--primary);
  margin-bottom:-1px}
"""


def _page(
    title: str, body: str, *, active: str | None = None, max_width: str = "560px"
) -> str:
    return page(title, body, active=active, extra_css=_EXTRA_CSS, max_width=max_width)


def render_entry() -> str:
    """시작 분기 — 이미 운영 중인 계정이 있는지부터 묻는다."""
    body = (
        '<div class="card"><h3>이미 운영 중인 계정이 있어요</h3>'
        "<p class='meta'>기존 SNS 계정을 등록하고, 인터뷰로 계정 컨셉을 잡아요.</p>"
        '<a href="/link"><button type="button">계정 연동하기</button></a></div>'
        '<div class="card"><h3>처음부터 새로 만들래요</h3>'
        "<p class='meta'>인터뷰로 컨셉을 먼저 잡고, 마지막에 계정을 만들어요.</p>"
        '<a href="/interview"><button type="button">새로 만들기</button></a></div>'
        '<p><a href="/channels">내 채널 목록 보기</a></p>'
    )
    return _page("온보딩 — 시작", f"<h1>SNS 계정이 이미 있으신가요?</h1>{body}")


def render_link(*, error: str | None = None) -> str:
    """연동할 기존 계정 정보 입력 — 여기서 채널을 만들지 않고 인터뷰로 넘긴다.

    실제 플랫폼 토큰 연동(OAuth, FR-W1)은 별도 단계 — 여기서는 계정 식별 정보만 받는다.
    """
    err = f'<p class="error">{escape(error)}</p>' if error else ""
    body = (
        f"{err}"
        '<form method="post" action="/link">'
        '<label class="choice"><input type="radio" name="platform" value="youtube" checked>'
        "유튜브 (쇼츠)</label>"
        '<label class="choice"><input type="radio" name="platform" value="instagram">'
        "인스타그램 (릴스)</label>"
        '<input type="text" name="handle" placeholder="계정 핸들 (예: my-channel)" required>'
        "<p class='meta'>등록은 인터뷰로 컨셉을 확정한 뒤에 완료됩니다.</p>"
        '<button type="submit">인터뷰 시작</button></form>'
        '<p><a href="/">← 처음으로</a></p>'
    )
    return _page("온보딩 — 계정 연동", f"<h1>어떤 계정을 연동할까요?</h1>{body}")


def render_channels(channels: tuple[ChannelRow, ...]) -> str:
    """만들어진 채널 목록 — 생성은 여기가 아니라 인터뷰 완료 화면에서 한다."""
    if not channels:
        rows = '<div class="card"><p class="empty">아직 만든 계정이 없습니다.</p></div>'
    else:
        cards = "".join(
            f'<div class="card"><h3>{_PLATFORM_ICON.get(c.platform, "📺")} '
            f'<a href="/channels/{c.channel_id}">{escape(c.handle)}</a></h3>'
            f'<div class="meta">{escape(c.platform)} · '
            f'<span class="badge neutral">{escape(c.mode)}</span></div></div>'
            for c in channels
        )
        rows = f'<div class="grid">{cards}</div>'
    start = (
        '<p style="margin-top:16px"><a href="/">'
        '<button type="button">새 계정 인터뷰 시작</button></a></p>'
    )
    body = (
        "<h1>채널</h1>"
        '<p class="page-sub">만들어진 계정과 프로필을 관리합니다</p>'
        f"{rows}{start}"
    )
    return _page("내 채널", body, active="channels", max_width="960px")


def render_compose(
    channels: tuple[ChannelRow, ...],
    *,
    selected: str | None = None,
    error: str | None = None,
) -> str:
    """새 포스트 — 줄글 영상 컨셉을 적으면 대본 생성까지 (feedr Composer 매핑).

    컨셉은 refine과 같은 경로로 프로필 revision에 반영된 뒤 대본 생성이 돈다.
    """
    # ponytail: 컨셉이 프로필 revision으로 영구히 남는다(기존 refine 동작 그대로) —
    # 1회성 지시가 필요해지면 run_profile_cycle에 컨셉 인자를 추가한다.
    head = (
        "<h1>새 포스트</h1>"
        '<p class="page-sub">영상 컨셉을 적으면 대본부터 만들어 드려요 — '
        "대본을 확인한 뒤 영상으로 만듭니다</p>"
    )
    if not channels:
        body = (
            f"{head}"
            '<div class="card"><p class="empty">먼저 채널을 만들어주세요.</p>'
            '<p style="text-align:center"><a href="/">'
            '<button type="button">계정 인터뷰 시작</button></a></p></div>'
        )
        return _page("새 포스트", body, active="compose", max_width="680px")
    err = f'<div class="error">{escape(error)}</div>' if error else ""
    picked = selected if any(c.channel_id == selected for c in channels) else channels[0].channel_id
    chips = "".join(
        f'<label class="chip-radio"><input type="radio" name="channel_id" '
        f'value="{escape(c.channel_id, quote=True)}"'
        f'{" checked" if c.channel_id == picked else ""}>'
        f"{_PLATFORM_ICON.get(c.platform, '📺')} {escape(c.handle)}</label>"
        for c in channels
    )
    body = (
        f"{head}{err}"
        f'<div class="card"><form method="post" action="/compose"{_SLOW_SUBMIT}>'
        "<label>채널</label>"
        f'<div class="chip-row">{chips}</div>'
        '<label for="compose-note">영상 컨셉</label>'
        '<textarea id="compose-note" name="note" placeholder="예: 신입 개발자 면접 꿀팁을 '
        '밈 스타일로, 이모지를 많이 (비워두면 채널 프로필 그대로)"></textarea>'
        '<button type="submit" class="big">대본 만들기</button></form></div>'
    )
    return _page("새 포스트", body, active="compose", max_width="680px")


def render_step(step: int, state: Mapping[str, str], *, error: str | None = None) -> str:
    builders = {
        1: _step_major,
        2: _step_subs,
        3: _step_tone,
        4: _step_goal,
        5: _step_character,
    }
    title, body = builders[step](state)
    pct = int((step - 1) / TOTAL_STEPS * 100)
    err = f'<p class="error">{escape(error)}</p>' if error else ""
    header = (
        f'<div class="step-label">{step} / {TOTAL_STEPS}</div>'
        f'<div class="progress"><div style="width:{pct}%"></div></div>'
    )
    return _page(f"온보딩 {step}/{TOTAL_STEPS}", f"{header}<h1>{title}</h1>{err}{body}")


def _hidden(state: Mapping[str, str], keys: tuple[str, ...]) -> str:
    return "".join(
        f'<input type="hidden" name="{k}" value="{escape(state[k], quote=True)}">'
        for k in keys
        if k in state
    )


# 추천안이 없거나 tune_ideas가 비었을 때의 미세조정 예시 기본값.
DEFAULT_TUNE_IDEAS: tuple[str, ...] = (
    "좀 더 초보자 눈높이로 설명해줘",
    "이모지를 많이 써줘",
    "세부 주제를 하나로 좁혀줘",
    "톤을 더 전문적으로 바꿔줘",
)


def _chips(values: tuple[str, ...], target_field: str) -> str:
    """누르면 같은 폼의 입력란(target_field)에 그 문구가 채워지는 제안 칩.

    값 자체를 textContent에서 읽으므로 JS 문자열 이스케이프 문제가 없다.
    """
    return "".join(
        f'<button type="button" class="chip" '
        f'onclick="this.form.{target_field}.value=this.textContent">{escape(v)}</button>'
        for v in values
    )


def _list_of_str(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(str(v).strip() for v in raw if str(v).strip())


def _tune_ideas(recommendation: Mapping[str, object] | None) -> tuple[str, ...]:
    if recommendation:
        ideas = _list_of_str(recommendation.get("tune_ideas"))
        if ideas:
            return ideas
    return DEFAULT_TUNE_IDEAS


def _tune_block(recommendation: Mapping[str, object] | None) -> str:
    return (
        "<p class='meta'>이런 걸 조정해볼 수 있어요 — 눌러서 채우기</p>"
        f"{_chips(_tune_ideas(recommendation), 'note')}"
    )


# 느린 제출(트렌드+LLM ~1분, 캐릭터 생성)의 중복 클릭 방지 — 제출이 시작된 뒤 버튼을
# 잠그고 클릭한 버튼의 문구만 바꾼다(이전 버튼 제외). 두 번 눌리면 추천 LLM이 중복
# 호출되고, 계정 만들기는 핸들 UNIQUE 위반 500이 났다(실사고).
_SLOW_SUBMIT = (
    ' onsubmit="const b=event.submitter;setTimeout(()=>{'
    "this.querySelectorAll('button').forEach(x=>x.disabled=true);"
    "if(b&&!b.formAction.includes('/back/'))b.textContent='처리 중… (최대 1분)'"
    '},0)"'
)


def _back_button(to_step: int) -> str:
    """같은 폼의 답(hidden 포함)을 실은 채 이전 화면을 다시 그린다 — 입력 유실 없음.

    `formnovalidate`: 현재 화면의 필수 입력을 건너뛰고 뒤로 갈 수 있게.
    """
    return (
        f'<button class="secondary" type="submit" formaction="/interview/back/{to_step}" '
        f"formnovalidate>← 이전</button> "
    )


def _radio(name: str, value: str, label: str, desc: str = "") -> str:
    d = f'<div class="desc">{escape(desc)}</div>' if desc else ""
    return (
        f'<label class="choice"><input type="radio" name="{name}" '
        f'value="{escape(value, quote=True)}">{escape(label)}{d}</label>'
    )


def _step_major(state: Mapping[str, str]) -> tuple[str, str]:
    choices = "".join(_radio("major", m, m) for m in TOPIC_MAJORS)
    body = (
        '<form method="post" action="/interview/step/2">'
        f"{_hidden(state, ('platform', 'handle'))}{choices}"
        '<input type="text" name="major_custom" placeholder="다른 주제 직접 입력">'
        '<button type="submit">다음</button></form>'
        '<p><a href="/">← 처음으로</a></p>'
    )
    return "어떤 주제로 계정을 만드시겠어요?", body


def _step_subs(state: Mapping[str, str]) -> tuple[str, str]:
    major = state.get("major", "")
    boxes = "".join(
        f'<label class="choice"><input type="checkbox" name="subs" '
        f'value="{escape(s, quote=True)}">{escape(s)}</label>'
        for s in TOPIC_MAJORS.get(major, ())
    )
    body = (
        '<form method="post" action="/interview/step/3">'
        f"{_hidden(state, ('platform', 'handle', 'major'))}{boxes}"
        '<input type="text" name="subs_custom" placeholder="직접 입력 (쉼표로 구분)">'
        f'<p class="meta">최대 {MAX_TOPIC_SUBS}개까지 고를 수 있어요.</p>'
        f'{_back_button(1)}<button type="submit">다음</button></form>'
    )
    return f"{major} 중에서 어떤 세부 주제를 다룰까요?", body


def _step_tone(state: Mapping[str, str]) -> tuple[str, str]:
    choices = "".join(_radio("tone", key, label) for key, label in TONES.items())
    body = (
        '<form method="post" action="/interview/step/4">'
        f"{_hidden(state, ('platform', 'handle', 'major', 'subs'))}{choices}"
        f'{_back_button(2)}<button type="submit">다음</button></form>'
    )
    return "계정의 전체적인 컨셉은요?", body


def _step_goal(state: Mapping[str, str]) -> tuple[str, str]:
    choices = "".join(
        _radio("goal_ref", ref, preset.label, preset.description)
        for ref, preset in GOAL_PRESETS.items()
    )
    body = (
        '<form method="post" action="/interview/step/5">'
        f"{_hidden(state, ('platform', 'handle', 'major', 'subs', 'tone'))}{choices}"
        f'{_back_button(3)}<button type="submit">다음</button></form>'
    )
    return "계정에서 가장 키우고 싶은 것은요?", body


def _step_character(state: Mapping[str, str]) -> tuple[str, str]:
    choices = "".join(_radio("style", key, label) for key, label in CHARACTER_STYLES.items())
    body = (
        f'<form method="post" action="/interview/finish"{_SLOW_SUBMIT}>'
        f"{_hidden(state, ('platform', 'handle', 'major', 'subs', 'tone', 'goal_ref'))}{choices}"
        '<p class="meta">영상에 등장할 캐릭터의 그림 스타일이에요.</p>'
        f'{_back_button(4)}<button type="submit">컨셉 확정</button></form>'
    )
    return "쇼츠·릴스에 캐릭터를 넣을까요?", body


def _summary_card(profile: ChannelProfile) -> str:
    goal_label = (
        GOAL_PRESETS[profile.goal_ref].label
        if profile.goal_ref in GOAL_PRESETS
        else profile.goal_ref
    )
    return (
        '<div class="card"><h3>계정 컨셉</h3>'
        f"<p>주제: {escape(profile.topic_major)} — {escape(', '.join(profile.topic_subs))}<br>"
        f"톤: {escape(TONES[profile.tone])}<br>"
        f"목표: {escape(goal_label)}<br>"
        f"캐릭터: {escape(CHARACTER_STYLES[profile.character_style])}</p></div>"
    )


def render_create(
    profile: ChannelProfile,
    recommendation: Mapping[str, object] | None,
    *,
    platform: str | None = None,
    handle: str | None = None,
) -> str:
    """인터뷰 완료 → 컨셉·추천 확인 + **여기서** 계정을 만들거나(신규) 연동을 완료한다.

    연동 경로(`platform`·`handle` 전달)는 시작 시 받은 계정 정보를 hidden으로 실어
    보낼 뿐, 실제 채널 등록은 두 경로 모두 이 화면의 버튼(POST /channels)에서 일어난다.
    """
    state = {
        "major": profile.topic_major,
        "subs": SUBS_SEP.join(profile.topic_subs),
        "tone": profile.tone,
        "goal_ref": profile.goal_ref,
        "style": profile.character_style,
    }
    if recommendation is not None:
        state["recommendation"] = json.dumps(recommendation, ensure_ascii=False)

    linked = platform is not None and handle is not None
    if linked:
        state["platform"] = platform or ""
        state["handle"] = handle or ""
        account_fields = (
            f"<p>연동할 계정: <b>{escape(platform or '')}</b> · <b>{escape(handle or '')}</b></p>"
        )
        title = "이 컨셉으로 계정 연동하기"
        button = "계정 연동하고 시작하기"
    else:
        name_ideas = _list_of_str(recommendation.get("name_ideas")) if recommendation else ()
        name_block = (
            "<p class='meta'>이런 채널 이름은 어때요? — 눌러서 채우기</p>"
            f"{_chips(name_ideas, 'handle')}"
            if name_ideas
            else ""
        )
        account_fields = (
            '<label class="choice"><input type="radio" name="platform" value="youtube" checked>'
            "유튜브 (쇼츠)</label>"
            '<label class="choice"><input type="radio" name="platform" value="instagram">'
            "인스타그램 (릴스)</label>"
            f"{name_block}"
            '<input type="text" name="handle" placeholder="채널 이름 (예: my-channel)" required>'
        )
        title = "이 컨셉으로 계정 만들기"
        button = "계정 만들기"

    body = (
        f"{_summary_card(profile)}{_recommendation_block(recommendation)}"
        f'<div class="card"><h3>{title}</h3>'
        f'<form method="post" action="/channels"{_SLOW_SUBMIT}>{_hidden(state, tuple(state))}'
        f"{account_fields}"
        "<p class='meta'>바꾸고 싶은 점이 있으면 한 줄로 적어주세요(선택). "
        "컨셉에 반영됩니다.</p>"
        f"{_tune_block(recommendation)}"
        '<textarea name="note" placeholder="예: 좀 더 초보자 눈높이로, 이모지를 많이"></textarea>'
        f'{_back_button(5)}<button type="submit">{button}</button></form></div>'
        '<a href="/">← 처음부터 다시 하기</a>'
    )
    return _page(
        "온보딩 — 컨셉 확정", f"<h1>이렇게 계정을 시작할게요</h1>{body}", max_width="640px"
    )


_PRE_STYLE = (
    'style="overflow-x:auto;background:#f6f8fa;padding:.8rem;border-radius:6px;font-size:.8rem"'
)
# body 안 meta refresh는 비표준이지만 주요 브라우저 전부에서 동작한다.
_AUTO_REFRESH = '<meta http-equiv="refresh" content="10">'


def _character_card(channel_id: str, profile: ChannelProfile, error: str | None) -> str:
    """캐릭터 카드 — 현재 이미지 표시 + 스타일 지정 (재)생성."""
    parts = ['<div class="card"><h3>캐릭터</h3>']
    if error:
        parts.append(f'<p class="error">{escape(error)}</p>')
    if profile.character_image_url is not None:
        parts.append(
            f'<img src="/channels/{channel_id}/character/image" alt="채널 캐릭터" '
            'style="width:180px;border-radius:8px">'
        )
        button = "다시 만들기"
    else:
        parts.append("<p class='meta'>아직 캐릭터가 없어요. 없으면 영상 배경은 주제에 맞는 "
                     "이미지로 대체됩니다.</p>")  # fmt: skip
        button = "캐릭터 만들기"
    styles = "".join(
        f'<label class="choice"><input type="radio" name="style" '
        f'value="{key}"{" checked" if key == profile.character_style else ""}>'
        f"{escape(label)}</label>"
        for key, label in CHARACTER_STYLES.items()
        if key != "none"
    )
    parts.append(
        f'<form method="post" action="/channels/{channel_id}/character"{_SLOW_SUBMIT}>'
        f'{styles}<button type="submit">{button}</button></form></div>'
    )
    return "".join(parts)


def render_channel(
    channel: ChannelRow,
    profile: ChannelProfile,
    *,
    video_enabled: bool = False,
    character_error: str | None = None,
) -> str:
    """채널 프로필 탭 — 컨셉·캐릭터·미세조정. 대본·영상은 영상 탭이 맡는다."""
    note = f"<p>운영자 지침: {escape(profile.note)}</p>" if profile.note else ""
    body = (
        _channel_head(channel, "profile", video_enabled=video_enabled)
        + f"{_summary_card(profile)}"
        f"{_character_card(channel.channel_id, profile, character_error)}"
        f"{_recommendation_block(profile.recommendation)}{note}"
        '<div class="card"><h3>미세 조정</h3>'
        "<p class='meta'>바꾸고 싶은 점을 한 줄로 적어주세요. 프로필에 반영됩니다.</p>"
        f'<form method="post" action="/channels/{channel.channel_id}/refine">'
        f"{_tune_block(profile.recommendation)}"
        '<textarea name="note" placeholder="예: 좀 더 초보자 눈높이로, 이모지를 많이"></textarea>'
        '<button type="submit">반영하기</button></form></div>'
        '<a href="/channels"><button class="secondary" type="button">내 채널 목록</button></a>'
    )
    return _page(
        f"계정 — {channel.handle}", body, active="channels", max_width="680px"
    )


def _script_card(item: VideoItemView, channel_id: str) -> str:
    """항목 1건 카드 — 대본은 표로, 완성 영상은 플레이어로."""
    parts = [f'<div class="card"><h3>{escape(item.topic)}</h3>']
    if item.state == "script":
        parts.append("<p class='meta'>대본 승인 대기 — 확인 후 영상으로 만드세요.</p>")
    elif item.state == "rendering":
        parts.append("<p>영상 렌더 중… 10초마다 자동 새로고침됩니다.</p>")
    elif item.state == "failed":
        parts.append('<p class="error">렌더 실패 — 로그 끝부분:</p>')
        parts.append(f"<pre {_PRE_STYLE}>{escape(item.log_tail)}</pre>")
    if item.slides:
        rows = "".join(
            f"<tr><td style='padding:.3rem .6rem;white-space:nowrap'><b>{escape(s)}</b></td>"
            f"<td style='padding:.3rem .6rem'>{escape(n)}</td></tr>"
            for s, n in item.slides
        )
        parts.append(f"<table style='border-collapse:collapse'>{rows}</table>")
    if item.body:
        parts.append(f'<p class="meta">캡션: {escape(item.body[:200])}</p>')
    if item.state == "done":
        parts.append(
            f'<video controls style="width:100%;max-height:480px" '
            f'src="/channels/{channel_id}/videos/{item.item_id}/media"></video>'
        )
    if item.state in ("script", "failed"):
        label = "영상 만들기" if item.state == "script" else "다시 렌더"
        parts.append(
            f'<form method="post" action="/channels/{channel_id}/videos/{item.item_id}/render">'
            f'<button type="submit">{label}</button></form>'
        )
    parts.append("</div>")
    return "".join(parts)


def render_videos(
    channel: ChannelRow,
    items: tuple[VideoItemView, ...],
    script_job: ScriptJobView | None,
) -> str:
    """영상 탭 — 대본 승인 → 렌더 → 완성 영상 관리. **생성 입구는 새 포스트 하나**다:
    같은 기능의 버튼을 두 곳에 두면 컨셉을 쓸 수 있는 쪽(새 포스트)만 남기는 게 맞다."""
    parts = [_channel_head(channel, "videos", video_enabled=True)]
    refreshing = script_job is not None and script_job.state == "running"
    refreshing = refreshing or any(i.state == "rendering" for i in items)
    if refreshing:
        parts.append(_AUTO_REFRESH)
    compose_url = f"/compose?channel={channel.channel_id}"
    if script_job is not None and script_job.state == "running":
        parts.append(
            '<div class="card"><p>대본 생성 중… (트렌드 조사 + 에이전트, 1~2분)</p></div>'
        )
    elif script_job is not None and script_job.state == "failed":
        parts.append(
            '<div class="card"><p class="error">대본 생성 실패 — 로그 끝부분:</p>'
            f"<pre {_PRE_STYLE}>{escape(script_job.log_tail)}</pre></div>"
        )
    else:
        parts.append(
            f'<p class="meta" style="margin-bottom:16px">새 대본은 '
            f'<a href="{compose_url}">새 포스트</a>에서 컨셉과 함께 만듭니다.</p>'
        )
    if not items:
        parts.append(
            '<div class="card"><p class="empty">아직 만든 대본이 없습니다.</p>'
            f'<p style="text-align:center"><a href="{compose_url}">'
            "<button type='button'>새 포스트에서 만들기</button></a></p></div>"
        )
    parts.extend(_script_card(i, channel.channel_id) for i in items)
    return _page(
        f"영상 관리 — {channel.handle}",
        "".join(parts),
        active="channels",
        max_width="760px",
    )


def _recommendation_block(recommendation: Mapping[str, object] | None) -> str:
    if not recommendation:
        return ""
    parts = ['<div class="card"><h3>트렌드 기반 추천</h3>']
    direction = recommendation.get("direction")
    if isinstance(direction, str):
        parts.append(f"<p>{escape(direction)}</p>")
    focus = recommendation.get("focus_subs")
    if isinstance(focus, list) and focus:
        parts.append(f"<p>우선 세부 주제: {escape(', '.join(str(s) for s in focus))}</p>")
    trends = recommendation.get("hot_trends")
    if isinstance(trends, list) and trends:
        items = "".join(f"<li>{escape(str(t))}</li>" for t in trends)
        parts.append(f'<p class="meta">근거 트렌드</p><ul>{items}</ul>')
    parts.append("</div>")
    return "".join(parts)


def render_not_found() -> str:
    body = (
        '<div class="card"><p class="empty">채널 또는 프로필을 찾을 수 없습니다.</p>'
        '<p style="text-align:center"><a href="/">← 처음으로</a></p></div>'
    )
    return _page("대상 없음", body, active="channels")
