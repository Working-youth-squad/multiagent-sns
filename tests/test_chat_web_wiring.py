"""챗봇 웹의 시드 계획 (`scripts/run_chat_web.plan_seed`) — DB 필요, LLM·렌더 0.

여기서 정하는 것은 **무엇을 어떤 프로필로 태울지**다. 틀리면 되돌릴 수 없다:
남의 주제 정책으로 렌더한 영상은 이미 만들어져 승인 대기로 들어간다.

`topic_major`가 없으면 조용히 개발 기본값으로 떨어지던 시절이 있었다 — 요리 채널에
코드 컷이 들어갔고, 그래서 그 기본값을 없앴다([sns.topic_policy]). 웹에서도 같은
규율을 지키는지 본다.
"""

import shutil

import psycopg
import pytest

from scripts.run_chat_web import (
    SeedRefused,
    plan_seed,
    tts_missing,
    wired_formats,
    wired_methods,
)
from sns.onboarding.profile import parse_profile, profile_to_json
from sns.onboarding.store import PgOnboardingStore

_PROFILE = {
    "topic_major": "개발",
    "topic_subs": ["파이썬"],
    "tone": "casual",
    "goal_ref": "engagement_depth",
    "character_style": "none",
}


def _channel(
    db: psycopg.Connection,
    *,
    handle: str,
    mode: str = "hybrid",
    platform: str = "instagram",
) -> str:
    row = db.execute(
        "INSERT INTO channel (platform, handle, mode, status) "
        "VALUES (%s, %s, %s, 'active') RETURNING id",
        (platform, handle, mode),
    ).fetchone()
    assert row is not None
    return str(row[0])


def _profile(db: psycopg.Connection, channel_id: str, **overrides: object) -> None:
    """온보딩이 쓰는 그 경로로 저장한다 — 테스트가 손으로 만든 jsonb를 넣으면
    파서를 통과 못 할 모양도 통과해 계약이 안 지켜진다."""
    PgOnboardingStore(db).save_profile(channel_id, parse_profile({**_PROFILE, **overrides}))


def test_no_hybrid_channel_is_refused_with_a_way_forward(db: psycopg.Connection) -> None:
    with pytest.raises(SeedRefused, match="온보딩"):
        plan_seed(db, choice="card")


def test_hybrid_without_a_profile_is_refused_not_defaulted(db: psycopg.Connection) -> None:
    """조용한 개발 기본값으로 떨어지면 요리 채널에 코드 컷이 들어간다."""
    _channel(db, handle="no-profile")
    with pytest.raises(SeedRefused, match="프로필"):
        plan_seed(db, choice="card")


def test_auto_channels_are_not_seeded(db: psycopg.Connection) -> None:
    """수동 시드는 hybrid에서만 돈다(FR-W5) — auto에 사람이 끼어들면 실험군이 오염된다."""
    auto = _channel(db, handle="auto-one", mode="auto")
    _profile(db, auto)
    with pytest.raises(SeedRefused):
        plan_seed(db, choice="card")


def test_channel_bound_conversation_seeds_only_that_channel(db: psycopg.Connection) -> None:
    """채널에 묶인 대화의 시드는 그 채널 하나만 태운다 — 채널별 관리의 배선 반쪽."""
    first = _channel(db, handle="one")
    _profile(db, first)
    _profile(db, _channel(db, handle="two"))
    plan = plan_seed(db, choice="card", channel_id=first)
    assert [t.channel_id for t in plan.targets] == [first]


def test_unknown_or_non_hybrid_channel_binding_is_refused(db: psycopg.Connection) -> None:
    auto = _channel(db, handle="auto-bound", mode="auto")
    _profile(db, auto)
    with pytest.raises(SeedRefused, match="hybrid"):
        plan_seed(db, choice="card", channel_id=auto)


def test_card_choice_targets_feed_image(db: psycopg.Connection) -> None:
    _profile(db, _channel(db, handle="ig-one"))
    plan = plan_seed(db, choice="card")
    assert [t.content_format for t in plan.targets] == ["feed_image"]
    assert plan.profile.topic_major == "개발"


def test_video_choice_follows_the_platform(db: psycopg.Connection) -> None:
    """릴스냐 쇼츠냐는 채널 플랫폼이 정한다 — 사람에게 물을 것이 아니다."""
    _profile(db, _channel(db, handle="ig-two", platform="instagram"))
    _profile(db, _channel(db, handle="yt-two", platform="youtube"))
    plan = plan_seed(db, choice="video")
    assert sorted(t.content_format for t in plan.targets) == ["reels", "shorts"]


def test_channels_with_another_topic_major_are_skipped_out_loud(db: psycopg.Connection) -> None:
    """사이클 하나에 topic_major 하나다. 조용히 빼면 왜 초안이 덜 나왔는지 알 수 없다."""
    _profile(db, _channel(db, handle="dev-ch"))
    _profile(db, _channel(db, handle="cook-ch"), topic_major="요리", topic_subs=["베이킹"])
    plan = plan_seed(db, choice="card")
    assert len(plan.targets) == 1
    assert plan.profile.topic_major == "개발"
    assert any("요리" in s for s in plan.skipped)


def test_profileless_channel_is_skipped_when_another_one_works(db: psycopg.Connection) -> None:
    """전부 막지는 않는다 — 되는 채널이 있으면 만들고, 빠진 것은 밝힌다."""
    _profile(db, _channel(db, handle="ok-ch"))
    _channel(db, handle="bare-ch")
    plan = plan_seed(db, choice="card")
    assert len(plan.targets) == 1
    assert any("프로필 없음" in s for s in plan.skipped)


def test_all_targets_are_hybrid(db: psycopg.Connection) -> None:
    """mode를 잘못 실으면 승인 관문 없이 자동 발행 경로로 들어간다."""
    _profile(db, _channel(db, handle="ig-three"))
    assert {t.mode for t in plan_seed(db, choice="card").targets} == {"hybrid"}


def test_store_roundtrip_matches_what_the_plan_reads(db: psycopg.Connection) -> None:
    """계획이 읽는 프로필은 온보딩이 쓴 그 값이다 — 두 경로가 갈리면 조용히 어긋난다."""
    channel_id = _channel(db, handle="ig-four")
    _profile(db, channel_id)
    stored = PgOnboardingStore(db).latest_profile(channel_id)
    assert stored is not None
    assert profile_to_json(plan_seed(db, choice="card").profile) == profile_to_json(stored)


# ── 대화에 뜨는 목록 = 이 환경이 실제로 되는 것 ────────────────────────


def _tts_ok(text: str, *, voice: str) -> bytes:
    return b"RIFF"


def _tts_denied(text: str, *, voice: str) -> bytes:
    raise RuntimeError("403 Caller does not have required permission\n(2번째 줄은 안 실린다)")


def test_video_is_hidden_without_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    """없는 것을 띄우면 사용자가 고른 뒤 분 단위를 기다린 끝에 실패를 만난다."""
    monkeypatch.setattr("scripts.run_chat_web.synthesize_google", _tts_ok)
    formats, missing = wired_formats("definitely-not-a-real-binary")
    assert formats == ("card",)
    assert any("ffmpeg" in m for m in missing)


def test_video_is_offered_when_everything_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.run_chat_web.synthesize_google", _tts_ok)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg 없음")
    assert wired_formats(ffmpeg) == (("card", "video"), [])


def test_working_tts_is_reported_as_available() -> None:
    assert tts_missing(_tts_ok) is None


def test_credentials_without_permission_count_as_missing() -> None:
    """자격 *존재*만 보면 부족하다 — ADC가 잡혀도 프로젝트에 권한이 없으면 렌더 직전
    403이 나고, 사용자는 몇 분을 기다린 끝에 그걸 만난다."""
    reason = tts_missing(_tts_denied)
    assert reason is not None
    assert "403" in reason
    # 스택트레이스가 아니라 한 줄이다 — 콘솔 한 줄과 대화 안내에 그대로 실린다.
    assert "\n" not in reason


def test_video_is_hidden_when_tts_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.run_chat_web.synthesize_google", _tts_denied)
    formats, missing = wired_formats(shutil.which("ffmpeg") or "ffmpeg")
    assert formats == ("card",)
    assert any("TTS" in m for m in missing)


# ── 유료 방식은 명시해야 켜진다 ────────────────────────────────────────


def test_methods_default_to_template_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """기본값에 유료 방식을 두면 결제가 켜진 계정에서 조용히 돈을 쓴다."""
    monkeypatch.delenv("CHAT_VIDEO_METHODS", raising=False)
    assert wired_methods() == ("template",)


def test_paid_method_is_opt_in_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAT_VIDEO_METHODS", "template, generated_scene")
    assert wired_methods() == ("template", "generated_scene")


def test_unknown_method_names_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """오타가 라우터에 흘러 들어가면 확정 뒤에야 막힌다 — 목록에서 빼는 편이 낫다."""
    monkeypatch.setenv("CHAT_VIDEO_METHODS", "template,generated_clip,오타")
    assert wired_methods() == ("template",)


def test_empty_env_never_yields_an_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """빈 목록을 넘기면 라우터가 아무것도 못 고른다."""
    monkeypatch.setenv("CHAT_VIDEO_METHODS", "  , ,")
    assert wired_methods() == ("template",)
