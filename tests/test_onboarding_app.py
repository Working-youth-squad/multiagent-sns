"""온보딩 위저드 앱 — 인터뷰 먼저, 계정 생성은 컨셉 확정 후 (InMemory, 네트워크 0)."""

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from httpx import Response

from sns.onboarding.profile import ChannelProfile
from sns.onboarding.store import InMemoryOnboardingStore
from sns.web.onboarding.app import create_app
from sns.web.onboarding.render import ScriptJobView, VideoItemView

_ANSWERS = {
    "major": "개발",
    "subs": "AI|파이썬",
    "tone": "casual",
    "goal_ref": "reach_growth",
    "style": "flat_vector",
}


def _client(store: InMemoryOnboardingStore, **collaborators: object) -> TestClient:
    return TestClient(create_app(store, **collaborators))  # type: ignore[arg-type]


def _walk_interview(client: TestClient) -> Response:
    """화면 1→5를 폼 체인으로 관통, 컨셉 확정(finish) 화면까지."""
    entry = client.get("/")
    assert "계정 연동하기" in entry.text and "새로 만들기" in entry.text  # 시작 분기
    assert client.get("/interview").status_code == 200
    r = client.post("/interview/step/2", data={"major": "개발"})
    assert "세부 주제" in r.text
    r = client.post("/interview/step/3", data={"major": "개발", "subs": ["AI", "파이썬"]})
    assert "컨셉" in r.text
    r = client.post(
        "/interview/step/4", data={"major": "개발", "subs": "AI|파이썬", "tone": "casual"}
    )
    assert "키우고 싶은" in r.text
    r = client.post(
        "/interview/step/5",
        data={"major": "개발", "subs": "AI|파이썬", "tone": "casual", "goal_ref": "reach_growth"},
    )
    assert "캐릭터" in r.text
    r = client.post("/interview/finish", data=_ANSWERS)
    assert r.status_code == 200
    assert "계정 만들기" in r.text  # 계정 생성은 컨셉 확정 후에야 가능하다
    return r


def _create_channel(client: TestClient, **extra: str) -> str:
    r = client.post(
        "/channels",
        data={**_ANSWERS, "platform": "youtube", "handle": "demo", **extra},
        follow_redirects=False,
    )
    assert r.status_code == 303
    return r.headers["location"].split("/")[2]


def test_interview_then_create_saves_profile() -> None:
    store = InMemoryOnboardingStore()
    client = _client(store)
    _walk_interview(client)
    assert store.list_channels() == ()  # 인터뷰만으로는 계정이 생기지 않는다

    channel_id = _create_channel(client)
    (channel,) = store.list_channels()
    assert channel.handle == "demo" and channel.mode == "hybrid"
    saved = store.latest_profile(channel_id)
    assert saved is not None
    assert saved.topic_subs == ("AI", "파이썬")
    assert saved.goal_ref == "reach_growth"
    assert saved.character_style == "flat_vector"

    detail = client.get(f"/channels/{channel_id}")
    assert "demo" in detail.text  # 채널 헤더(핸들)
    assert "프로필" in detail.text  # 탭바
    assert "플랫 벡터" in detail.text


def test_step_validation_reprompts_with_error() -> None:
    client = _client(InMemoryOnboardingStore())
    r = client.post("/interview/step/2", data={})
    assert "주제를 선택하거나" in r.text
    r = client.post(
        "/interview/step/3", data={"major": "개발", "subs": ["a", "b", "c"], "subs_custom": "d"}
    )
    assert "최대 3개" in r.text
    # 직접 입력만으로도 진행 가능
    r = client.post("/interview/step/3", data={"major": "원예", "subs_custom": "분재, 화분"})
    assert "컨셉" in r.text


def test_name_and_tune_idea_chips_on_finish() -> None:
    client = _client(
        InMemoryOnboardingStore(),
        recommend_fn=lambda p: {
            "direction": "방향",
            "name_ideas": ["코드 스낵"],
            "tune_ideas": ["이모지를 많이 써줘"],
        },
    )
    finish = _walk_interview(client)
    assert "이런 채널 이름은 어때요?" in finish.text
    assert "코드 스낵" in finish.text  # 이름 추천 칩
    assert "이모지를 많이 써줘" in finish.text  # 미세조정 예시 칩


def test_default_tune_ideas_without_recommendation() -> None:
    store = InMemoryOnboardingStore()
    client = _client(store)  # recommend_fn 없음
    finish = _walk_interview(client)
    assert "조정해볼 수 있어요" in finish.text
    assert "좀 더 초보자 눈높이로 설명해줘" in finish.text  # 기본 예시로 폴백
    assert "이런 채널 이름은 어때요?" not in finish.text  # 추천 없으면 이름 칩도 없음

    # 계정 페이지의 미세조정 폼에도 예시 칩이 뜬다.
    ch = _create_channel(client)
    detail = client.get(f"/channels/{ch}")
    assert "좀 더 초보자 눈높이로 설명해줘" in detail.text


def test_recommendation_shown_at_finish_and_persisted_on_create() -> None:
    store = InMemoryOnboardingStore()
    client = _client(store, recommend_fn=lambda p: {"direction": "AI 뉴스 요약 중심"})
    finish = _walk_interview(client)
    assert "AI 뉴스 요약 중심" in finish.text  # 확정 화면에서 추천 표시

    # 확정 화면의 hidden recommendation이 생성 시 프로필에 박제된다.
    ch = _create_channel(client, recommendation='{"direction": "AI 뉴스 요약 중심"}')
    saved = store.latest_profile(ch)
    assert saved is not None and saved.recommendation == {"direction": "AI 뉴스 요약 중심"}


def test_recommend_failure_does_not_block_finish() -> None:
    def boom(profile: ChannelProfile) -> dict[str, object]:
        raise RuntimeError("network down")

    client = _client(InMemoryOnboardingStore(), recommend_fn=boom)
    finish = _walk_interview(client)  # 추천 실패해도 확정 화면 도달
    assert "계정 만들기" in finish.text


def test_character_generated_only_at_create() -> None:
    store = InMemoryOnboardingStore()
    calls: list[str] = []

    def stamp(profile: ChannelProfile) -> ChannelProfile:
        calls.append(profile.character_style)
        return replace(profile, character_image_url="mem://char", character_checksum="c1")

    client = _client(store, ensure_character_fn=stamp)
    _walk_interview(client)
    assert calls == []  # 인터뷰·확정 화면까지는 유료 호출 0회

    ch = _create_channel(client)
    assert calls == ["flat_vector"]  # 계정 생성 시점에만 1회
    saved = store.latest_profile(ch)
    assert saved is not None and saved.character_image_url == "mem://char"


def test_create_with_note_applies_refine_or_fallback() -> None:
    store = InMemoryOnboardingStore()

    def refine(profile: ChannelProfile, note: str) -> ChannelProfile:
        return replace(profile, topic_subs=("AI",), note=note)

    client = _client(store, refine_fn=refine)
    _walk_interview(client)
    ch = _create_channel(client, note="AI만 다루자")
    saved = store.latest_profile(ch)
    assert saved is not None
    assert saved.topic_subs == ("AI",) and saved.note == "AI만 다루자"

    # refine_fn 없으면 줄글이 note로 보존된다.
    store2 = InMemoryOnboardingStore()
    client2 = _client(store2)
    _walk_interview(client2)
    ch2 = _create_channel(client2, note="줄글 폴백")
    saved2 = store2.latest_profile(ch2)
    assert saved2 is not None and saved2.note == "줄글 폴백"


def test_refine_after_create_appends_revision() -> None:
    store = InMemoryOnboardingStore()
    client = _client(store)
    _walk_interview(client)
    ch = _create_channel(client)

    r = client.post(
        f"/channels/{ch}/refine", data={"note": "초보자 눈높이로"}, follow_redirects=False
    )
    assert r.status_code == 303
    latest = store.latest_profile(ch)
    assert latest is not None and latest.note == "초보자 눈높이로"
    assert len(store.profiles[ch]) == 2  # 개정 = 새 revision


def test_link_path_carries_account_through_interview() -> None:
    """연동 경로 — 계정 정보가 위저드를 관통해 확정 화면에서 그 계정으로 등록된다."""
    store = InMemoryOnboardingStore()
    client = _client(store)

    r = client.post("/link", data={"platform": "instagram", "handle": "my-insta"})
    assert "1 / 5" in r.text  # 바로 인터뷰 시작
    assert 'value="my-insta"' in r.text  # hidden으로 실려 간다

    acct = {"platform": "instagram", "handle": "my-insta"}
    r = client.post("/interview/step/2", data={**acct, "major": "요리"})
    r = client.post("/interview/step/3", data={**acct, "major": "요리", "subs": ["비건"]})
    r = client.post(
        "/interview/step/4", data={**acct, "major": "요리", "subs": "비건", "tone": "casual"}
    )
    r = client.post(
        "/interview/step/5",
        data={
            **acct,
            "major": "요리",
            "subs": "비건",
            "tone": "casual",
            "goal_ref": "reach_growth",
        },
    )
    r = client.post(
        "/interview/finish",
        data={
            **acct,
            "major": "요리",
            "subs": "비건",
            "tone": "casual",
            "goal_ref": "reach_growth",
            "style": "none",
        },
    )
    assert "계정 연동하고 시작하기" in r.text  # 신규 폼 대신 연동 확정 버튼
    assert "my-insta" in r.text
    assert store.list_channels() == ()  # 확정 전에는 채널 미등록

    r = client.post(
        "/channels",
        data={
            **acct,
            "major": "요리",
            "subs": "비건",
            "tone": "casual",
            "goal_ref": "reach_growth",
            "style": "none",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    (channel,) = store.list_channels()
    assert channel.platform == "instagram" and channel.handle == "my-insta"


def test_link_requires_handle() -> None:
    client = _client(InMemoryOnboardingStore())
    r = client.post("/link", data={"platform": "youtube", "handle": "  "})
    assert "계정 핸들을 입력해주세요" in r.text


def test_back_rerenders_previous_step_with_state() -> None:
    """이전 버튼 — 지금까지의 답과 연동 계정 정보가 유실 없이 이전 화면에 실린다."""
    client = _client(InMemoryOnboardingStore())
    r = client.post(
        "/interview/back/3",
        data={
            "major": "개발",
            "subs": "AI|파이썬",
            "tone": "casual",
            "platform": "instagram",
            "handle": "my-insta",
        },
    )
    assert "컨셉" in r.text  # 3/5(톤) 화면 재표시
    assert 'value="AI|파이썬"' in r.text  # 세부 주제 답 보존
    assert 'value="my-insta"' in r.text  # 연동 계정 정보 보존
    # 잘못된 step은 404
    assert client.post("/interview/back/9", data={}).status_code == 404


def test_every_step_has_back_control() -> None:
    client = _client(InMemoryOnboardingStore())
    assert "← 처음으로" in client.get("/interview").text  # 1/5
    r = client.post("/interview/step/2", data={"major": "개발"})
    assert "/interview/back/1" in r.text  # 2/5 → 1/5
    finish = client.post("/interview/finish", data=_ANSWERS)
    assert "/interview/back/5" in finish.text  # 컨셉 확정 → 5/5


def test_channels_list_and_missing_detail() -> None:
    store = InMemoryOnboardingStore()
    client = _client(store)
    assert "아직 만든 계정이 없습니다" in client.get("/channels").text
    assert client.get("/channels/nope").status_code == 404


class FakeVideoManager:
    """호출 기록 + 미리 정한 항목/작업 뷰 — 서브프로세스·DB 없음."""

    def __init__(
        self,
        items: tuple[VideoItemView, ...] = (),
        script: ScriptJobView | None = None,
    ) -> None:
        self.script_started: list[str] = []
        self.render_started: list[tuple[str, str]] = []
        self._items = items
        self._script = script

    def start_script(self, handle: str) -> None:
        self.script_started.append(handle)

    def start_render(self, handle: str, item_id: str) -> None:
        self.render_started.append((handle, item_id))

    def items(self, handle: str) -> tuple[VideoItemView, ...]:
        return self._items

    def script_job(self, handle: str) -> ScriptJobView | None:
        return self._script


def test_videos_tab_script_first_then_render(tmp_path: Path) -> None:
    """대본 승인 대기 → '영상 만들기' 수락 → 완성 mp4 서빙까지 탭 플로우."""
    store = InMemoryOnboardingStore()
    script_item = VideoItemView(
        item_id="item-1", topic="혈당 베이킹", state="script",
        slides=(("대체당", "설탕 없이 굽는 법을 알려드려요."),), body="캡션",
    )  # fmt: skip
    manager = FakeVideoManager(items=(script_item,))
    client = _client(store, video_manager=manager)
    _walk_interview(client)
    ch = _create_channel(client)

    detail = client.get(f"/channels/{ch}")
    assert f"/channels/{ch}/videos" in detail.text  # 채널 페이지 → 영상 관리 탭 링크

    tab = client.get(f"/channels/{ch}/videos")
    # 생성 입구는 새 포스트로 통일 — 탭에는 링크만 남는다.
    assert f"/compose?channel={ch}" in tab.text
    assert "대본 승인 대기" in tab.text and "설탕 없이 굽는 법" in tab.text
    assert "영상 만들기" in tab.text  # 수락 버튼 — 영상은 아직 없다

    r = client.post(f"/channels/{ch}/videos/script", follow_redirects=False)
    assert r.status_code == 303 and manager.script_started == ["demo"]
    r = client.post(f"/channels/{ch}/videos/item-1/render", follow_redirects=False)
    assert r.status_code == 303 and manager.render_started == [("demo", "item-1")]
    assert client.post(f"/channels/{ch}/videos/nope/render").status_code == 404

    mp4 = tmp_path / "video-abc.mp4"
    mp4.write_bytes(b"fake-mp4")
    done = VideoItemView(item_id="item-1", topic="혈당 베이킹", state="done",
                         video_path=str(mp4))  # fmt: skip
    done_client = _client(store, video_manager=FakeVideoManager(items=(done,)))
    page = done_client.get(f"/channels/{ch}/videos")
    assert f"/channels/{ch}/videos/item-1/media" in page.text
    served = done_client.get(f"/channels/{ch}/videos/item-1/media")
    assert served.status_code == 200 and served.content == b"fake-mp4"


def test_videos_tab_shows_running_script_job() -> None:
    store = InMemoryOnboardingStore()
    client = _client(store, video_manager=FakeVideoManager(script=ScriptJobView("running")))
    _walk_interview(client)
    ch = _create_channel(client)
    tab = client.get(f"/channels/{ch}/videos")
    assert "대본 생성 중" in tab.text


def test_compose_form_lists_channels_or_prompts_onboarding() -> None:
    store = InMemoryOnboardingStore()
    client = _client(store, video_manager=FakeVideoManager())
    empty = client.get("/compose")
    assert "먼저 채널을 만들어주세요" in empty.text

    _walk_interview(client)
    _create_channel(client)
    form = client.get("/compose")
    assert "새 포스트" in form.text and "영상 컨셉" in form.text
    assert "demo" in form.text  # 채널 선택 칩
    assert "대본 만들기" in form.text


def test_compose_applies_note_and_starts_script() -> None:
    """줄글 컨셉 → 프로필 revision 반영(refine과 같은 경로) → 대본 생성 기동."""
    store = InMemoryOnboardingStore()
    manager = FakeVideoManager()
    client = _client(store, video_manager=manager)
    _walk_interview(client)
    ch = _create_channel(client)

    r = client.post(
        "/compose",
        data={"channel_id": ch, "note": "밈 스타일로, 이모지를 많이"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/channels/{ch}/videos"
    assert manager.script_started == ["demo"]
    saved = store.latest_profile(ch)
    assert saved is not None and saved.note == "밈 스타일로, 이모지를 많이"

    # 빈 컨셉은 프로필을 건드리지 않고 대본 생성만 돈다.
    r = client.post("/compose", data={"channel_id": ch}, follow_redirects=False)
    assert r.status_code == 303 and manager.script_started == ["demo", "demo"]

    assert client.post("/compose", data={"channel_id": "nope"}).status_code == 404

    # 영상 탭에서 넘어오는 ?channel= 프리셀렉트
    form = client.get(f"/compose?channel={ch}")
    assert f'value="{ch}" checked' in form.text


def test_compose_without_manager_shows_error() -> None:
    store = InMemoryOnboardingStore()
    client = _client(store)
    _walk_interview(client)
    ch = _create_channel(client)
    r = client.post("/compose", data={"channel_id": ch})
    assert r.status_code == 200 and "영상 배선이 없습니다" in r.text
    assert store.latest_profile(ch) is not None
    assert store.latest_profile(ch).note is None  # 배선 없으면 프로필도 무변경


def test_video_routes_404_without_manager() -> None:
    client = _client(InMemoryOnboardingStore())
    _walk_interview(client)
    ch = _create_channel(client)
    assert f"/channels/{ch}/videos" not in client.get(f"/channels/{ch}").text  # 영상 탭 숨김
    assert client.get(f"/channels/{ch}/videos").status_code == 404
    assert client.post(f"/channels/{ch}/videos/script").status_code == 404


def test_character_regenerate_with_user_style(tmp_path: Path) -> None:
    """캐릭터 카드 — 사용자가 스타일을 지정해 (재)생성하고, 실패는 화면에 표면화된다."""
    store = InMemoryOnboardingStore()
    png = tmp_path / "char.png"
    png.write_bytes(b"fake-png")
    calls: list[tuple[str, str | None]] = []

    def fake_ensure(profile: ChannelProfile) -> ChannelProfile:
        calls.append((profile.character_style, profile.character_image_url))
        return replace(profile, character_image_url=png.resolve().as_uri(), character_checksum="c")

    client = _client(store, ensure_character_fn=fake_ensure)
    _walk_interview(client)
    ch = _create_channel(client)  # 생성 시 1회
    assert (
        "캐릭터 만들기" in client.get(f"/channels/{ch}").text
        or "다시 만들기" in client.get(f"/channels/{ch}").text
    )

    r = client.post(f"/channels/{ch}/character", data={"style": "pixel_art"},
                    follow_redirects=False)  # fmt: skip
    assert r.status_code == 303
    assert calls[-1] == ("pixel_art", None)  # URL을 비워 재생성을 강제한다
    saved = store.latest_profile(ch)
    assert saved is not None and saved.character_style == "pixel_art"
    assert saved.character_image_url is not None

    img = client.get(f"/channels/{ch}/character/image")
    assert img.status_code == 200 and img.content == b"fake-png"


def test_character_failure_shown_not_swallowed() -> None:
    def boom(profile: ChannelProfile) -> ChannelProfile:
        raise RuntimeError("429 크레딧 없음")

    store = InMemoryOnboardingStore()
    client = _client(store)
    _walk_interview(client)
    ch = _create_channel(client)

    failing = _client(store, ensure_character_fn=boom)
    r = failing.post(f"/channels/{ch}/character", data={"style": "pixel_art"})
    assert r.status_code == 502 and "캐릭터 생성 실패" in r.text
    saved = store.latest_profile(ch)
    assert saved is not None and saved.character_image_url is None  # 프로필은 그대로
