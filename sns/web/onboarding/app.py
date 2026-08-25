"""온보딩 인터뷰 FastAPI 앱 — HTTP 어댑터 ([sns.web.approve.app] 규율 동형).

플로우: `/`에서 **기존 계정 연동 vs 새로 만들기**로 분기한다. 연동(`/link`)은 계정
정보만 먼저 받아 위저드 state로 실어 나르고, 두 경로 모두 인터뷰(1~5) → 컨셉 확정
화면 → `POST /channels`에서야 채널이 등록된다 — 중도 이탈 시 빈 채널이 남지 않는다.
캐릭터 생성(유료)도 이 시점에만 1회라 인터뷰만 하고 떠난 사용자에게 비용이 없다.

`create_app(store, ...)`은 배선된 `OnboardingStore`와 선택 협력자 3개를 주입받는다:

- `recommend_fn`: 프로필 → 트렌드 기반 추천안(dict). 트렌드·LLM 배선은 호출자 몫 —
  None이면 추천 없이 진행한다(탈부착 심: 상세 트렌드 조립은 별도 작업 영역).
- `refine_fn`: (프로필, 줄글) → 개정 프로필. None이면 줄글을 `note`로만 보존한다
  (note는 [sns.onboarding.profile.build_channel_brief]가 에이전트 지침에 포함).
- `ensure_character_fn`: 프로필 → 캐릭터 이미지 박제된 프로필(유료 생성 — 실패 허용).

세 협력자의 어떤 실패도 온보딩을 막지 않는다 — 인터뷰 완주가 항상 우선이다.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import replace

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from sns.onboarding.profile import ChannelProfile, ProfileError, parse_profile
from sns.onboarding.store import OnboardingStore
from sns.web.onboarding.render import (
    SUBS_SEP,
    render_channel,
    render_channels,
    render_create,
    render_entry,
    render_link,
    render_not_found,
    render_step,
)

RecommendFn = Callable[[ChannelProfile], Mapping[str, object] | None]
RefineFn = Callable[[ChannelProfile, str], ChannelProfile]
CharacterFn = Callable[[ChannelProfile], ChannelProfile]


def _account_state(platform: str, handle: str) -> dict[str, str]:
    """연동 경로에서만 채워지는 계정 정보 — 있을 때만 위저드 state에 실어 나른다."""
    if platform.strip() and handle.strip():
        return {"platform": platform.strip(), "handle": handle.strip()}
    return {}


def _build_profile(
    *, major: str, subs: str, tone: str, goal_ref: str, style: str
) -> ChannelProfile:
    return parse_profile(
        {
            "topic_major": major,
            "topic_subs": subs.split(SUBS_SEP),
            "tone": tone,
            "goal_ref": goal_ref,
            "character": {"style": style},
        }
    )


def create_app(
    store: OnboardingStore,
    *,
    recommend_fn: RecommendFn | None = None,
    refine_fn: RefineFn | None = None,
    ensure_character_fn: CharacterFn | None = None,
) -> FastAPI:
    app = FastAPI(title="온보딩 인터뷰")

    @app.get("/", response_class=HTMLResponse)
    def entry() -> HTMLResponse:
        """시작 분기 — 기존 계정 연동 vs 새로 만들기."""
        return HTMLResponse(render_entry())

    @app.get("/interview", response_class=HTMLResponse)
    def start_new() -> HTMLResponse:
        return HTMLResponse(render_step(1, {}))

    @app.get("/link", response_class=HTMLResponse)
    def link_form() -> HTMLResponse:
        return HTMLResponse(render_link())

    @app.post("/link", response_class=HTMLResponse)
    def link_submit(
        platform: str = Form(default="youtube"), handle: str = Form(default="")
    ) -> HTMLResponse:
        """연동할 계정 정보를 받아 인터뷰로 — 채널 등록은 컨셉 확정 후에 한다."""
        if not handle.strip():
            return HTMLResponse(render_link(error="계정 핸들을 입력해주세요."))
        return HTMLResponse(render_step(1, {"platform": platform, "handle": handle.strip()}))

    @app.post("/interview/back/{step}", response_class=HTMLResponse)
    def back(
        step: int,
        major: str = Form(default=""),
        subs: str = Form(default=""),
        tone: str = Form(default=""),
        goal_ref: str = Form(default=""),
        platform: str = Form(default=""),
        handle: str = Form(default=""),
    ) -> HTMLResponse:
        """이전 화면으로 — 지금까지의 답(hidden)을 그대로 실어 다시 그린다."""
        if step not in (1, 2, 3, 4, 5):
            return HTMLResponse(render_not_found(), status_code=404)
        state = _account_state(platform, handle)
        for key, value in (
            ("major", major),
            ("subs", subs),
            ("tone", tone),
            ("goal_ref", goal_ref),
        ):
            if value.strip():
                state[key] = value.strip()
        return HTMLResponse(render_step(step, state))

    @app.post("/interview/step/2", response_class=HTMLResponse)
    def step2(
        major: str = Form(default=""),
        major_custom: str = Form(default=""),
        platform: str = Form(default=""),
        handle: str = Form(default=""),
    ) -> HTMLResponse:
        state = _account_state(platform, handle)
        chosen = major_custom.strip() or major.strip()
        if not chosen:
            return HTMLResponse(render_step(1, state, error="주제를 선택하거나 직접 입력해주세요."))
        return HTMLResponse(render_step(2, {**state, "major": chosen}))

    @app.post("/interview/step/3", response_class=HTMLResponse)
    def step3(
        major: str = Form(...),
        subs: list[str] = Form(default_factory=list),  # noqa: B008 — FastAPI 다중 체크박스 관례
        subs_custom: str = Form(default=""),
        platform: str = Form(default=""),
        handle: str = Form(default=""),
    ) -> HTMLResponse:
        picked = [*subs, *(s.strip() for s in subs_custom.split(",") if s.strip())]
        unique = list(dict.fromkeys(s.strip() for s in picked if s.strip()))
        state = {**_account_state(platform, handle), "major": major}
        if not unique:
            return HTMLResponse(render_step(2, state, error="세부 주제를 1개 이상 골라주세요."))
        if len(unique) > 3:
            return HTMLResponse(render_step(2, state, error="세부 주제는 최대 3개까지예요."))
        return HTMLResponse(render_step(3, {**state, "subs": SUBS_SEP.join(unique)}))

    @app.post("/interview/step/4", response_class=HTMLResponse)
    def step4(
        major: str = Form(...),
        subs: str = Form(...),
        tone: str = Form(default=""),
        platform: str = Form(default=""),
        handle: str = Form(default=""),
    ) -> HTMLResponse:
        state = {**_account_state(platform, handle), "major": major, "subs": subs}
        if not tone:
            return HTMLResponse(render_step(3, state, error="컨셉을 골라주세요."))
        return HTMLResponse(render_step(4, {**state, "tone": tone}))

    @app.post("/interview/step/5", response_class=HTMLResponse)
    def step5(
        major: str = Form(...),
        subs: str = Form(...),
        tone: str = Form(...),
        goal_ref: str = Form(default=""),
        platform: str = Form(default=""),
        handle: str = Form(default=""),
    ) -> HTMLResponse:
        state = {
            **_account_state(platform, handle),
            "major": major,
            "subs": subs,
            "tone": tone,
        }
        if not goal_ref:
            return HTMLResponse(render_step(4, state, error="목표를 골라주세요."))
        return HTMLResponse(render_step(5, {**state, "goal_ref": goal_ref}))

    @app.post("/interview/finish", response_class=HTMLResponse)
    def finish(
        major: str = Form(...),
        subs: str = Form(...),
        tone: str = Form(...),
        goal_ref: str = Form(...),
        style: str = Form(default=""),
        platform: str = Form(default=""),
        handle: str = Form(default=""),
    ) -> HTMLResponse:
        account = _account_state(platform, handle)
        state = {**account, "major": major, "subs": subs, "tone": tone, "goal_ref": goal_ref}
        if not style:
            return HTMLResponse(render_step(5, state, error="캐릭터 스타일을 골라주세요."))
        try:
            profile = _build_profile(
                major=major, subs=subs, tone=tone, goal_ref=goal_ref, style=style
            )
        except ProfileError as e:
            return HTMLResponse(render_step(5, state, error=str(e)))

        recommendation: Mapping[str, object] | None = None
        if recommend_fn is not None:
            try:
                recommendation = recommend_fn(profile)
            except Exception:  # 추천 실패(네트워크·LLM)는 온보딩을 막지 않는다
                recommendation = None
        return HTMLResponse(
            render_create(
                profile,
                recommendation,
                platform=account.get("platform"),
                handle=account.get("handle"),
            )
        )

    @app.post("/channels", response_model=None)
    def create_channel(
        platform: str = Form(...),
        handle: str = Form(...),
        major: str = Form(...),
        subs: str = Form(...),
        tone: str = Form(...),
        goal_ref: str = Form(...),
        style: str = Form(...),
        recommendation: str = Form(default=""),
        note: str = Form(default=""),
    ) -> HTMLResponse | RedirectResponse:
        """컨셉 확정 후의 실제 계정 생성 — 프로필 저장·캐릭터 생성이 여기서 일어난다."""
        state = {"major": major, "subs": subs, "tone": tone, "goal_ref": goal_ref}
        try:
            profile = _build_profile(
                major=major, subs=subs, tone=tone, goal_ref=goal_ref, style=style
            )
        except ProfileError as e:
            return HTMLResponse(render_step(5, state, error=str(e)))

        if recommendation.strip():
            try:
                rec = json.loads(recommendation)
            except ValueError:
                rec = None
            if isinstance(rec, dict):
                profile = replace(profile, recommendation=rec)

        text = note.strip()
        if text:
            revised = profile
            if refine_fn is not None:
                try:
                    revised = refine_fn(profile, text)
                except Exception:  # LLM 실패 시 줄글 원문 보존으로 폴백
                    revised = profile
            profile = revised if revised != profile else replace(profile, note=text)

        # 캐릭터 생성(유료)은 계정 생성이 확정된 이 시점에만 1회.
        if ensure_character_fn is not None and profile.character_style != "none":
            try:
                profile = ensure_character_fn(profile)
            except Exception:  # 캐릭터 생성 실패도 온보딩을 막지 않는다
                pass

        channel_id = store.create_channel(platform=platform, handle=handle.strip())
        store.save_profile(channel_id, profile)
        return RedirectResponse(f"/channels/{channel_id}", status_code=303)

    @app.get("/channels", response_class=HTMLResponse)
    def channels() -> HTMLResponse:
        return HTMLResponse(render_channels(store.list_channels()))

    @app.get("/channels/{channel_id}", response_model=None)
    def channel_detail(channel_id: str) -> HTMLResponse | RedirectResponse:
        channel = next((c for c in store.list_channels() if c.channel_id == channel_id), None)
        profile = store.latest_profile(channel_id)
        if channel is None or profile is None:
            return HTMLResponse(render_not_found(), status_code=404)
        return HTMLResponse(render_channel(channel, profile))

    @app.post("/channels/{channel_id}/refine", response_model=None)
    def refine(channel_id: str, note: str = Form(default="")) -> HTMLResponse | RedirectResponse:
        profile = store.latest_profile(channel_id)
        if profile is None:
            return HTMLResponse(render_not_found(), status_code=404)
        text = note.strip()
        if text:
            revised = profile
            if refine_fn is not None:
                try:
                    revised = refine_fn(profile, text)
                except Exception:  # LLM 실패 시 줄글 원문 보존으로 폴백
                    revised = profile
            if revised == profile:
                revised = replace(profile, note=text)
            store.save_profile(channel_id, revised)  # 개정 = 새 revision (FR-W2)
        return RedirectResponse(f"/channels/{channel_id}", status_code=303)

    return app
