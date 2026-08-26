"""포맷별 렌더 배선 정본 ([sns.runner.wiring]). 네트워크·ffmpeg·DB 0.

**여기 있는 이유가 곧 이 모듈이 생긴 이유다.** 이 배선이 진입점마다 흩어져 있었고,
옛 판이 지워지지 않은 채 새 판 뒤에 남아 `--format video --style 3col`이 영상 라우터를
카드 렌더러로 덮어썼다 — 영상을 요청했는데 이미지가 나갔다. 아래 첫 두 테스트가 그
모양을 정확히 겨눈다.
"""

from pathlib import Path

import pytest

from sns.render.card.media import CardRenderMedia
from sns.render.storage import InMemoryMediaStore
from sns.render.video.gen.media import SceneRenderMedia
from sns.render.video.media import VideoRenderMedia
from sns.render.video.router import VideoRenderRouter
from sns.runner.wiring import (
    VIDEO_STYLES,
    build_render_wiring,
    extras_only_resolve,
    spec_style,
    style_guidance,
)
from sns.topic_policy import DEV_MAJOR


def _build(**kwargs: object):  # type: ignore[no-untyped-def]
    base: dict[str, object] = {
        "kind": "video",
        "store": InMemoryMediaStore(),
        "topic_major": DEV_MAJOR,
    }
    base.update(kwargs)
    return build_render_wiring(**base)  # type: ignore[arg-type]


# ── 포맷이 렌더러를 정한다 (되돌아온 사고를 겨눈다) ────────────────────


@pytest.mark.parametrize("style", VIDEO_STYLES)
def test_video_keeps_the_router_for_every_style(style: str) -> None:
    """화면 문법이 렌더러 종류를 바꾸지 않는다.

    예전엔 `style=3col`이 카드 렌더러로 덮어써졌다 — style은 **영상 안의** 문법이지
    영상이냐 카드냐의 축이 아니다.
    """
    wiring = _build(style=style)
    assert isinstance(wiring.render_media, VideoRenderRouter)
    assert wiring.resolve_media_spec is not None


def test_card_format_uses_the_card_renderer() -> None:
    wiring = _build(kind="card")
    assert isinstance(wiring.render_media, CardRenderMedia)
    # 카드에는 해소가 없다 — 정사각 사진·장면은 영상의 개념이다.
    assert wiring.resolve_media_spec is None


def test_one_wiring_serves_both_reels_and_shorts() -> None:
    """릴스와 쇼츠는 배선이 같다 — 다른 것은 규격뿐이고 그건 spec이 정한다.

    `ContentFormat`을 받던 시절엔 인스타 릴스 + 유튜브 쇼츠를 한 사이클에 태울 때
    "어느 대상의 포맷을 넘길 것인가"라는 답 없는 질문이 생겼다.
    """
    assert isinstance(_build(kind="video").render_media, VideoRenderRouter)


# ── Capability Gate ───────────────────────────────────────────────────


def test_only_wired_methods_are_selectable() -> None:
    """라우터에 안 적힌 방식은 에이전트가 고를 수도 없다."""
    assert _build().supported_methods == ("template",)


def test_paid_method_requires_an_explicit_request() -> None:
    """기본값에 두면 결제가 켜진 계정에서 사이클이 조용히 돈을 쓴다."""
    wiring = _build(methods=("template", "generated_scene"))
    assert set(wiring.supported_methods) == {"template", "generated_scene"}
    renderers = wiring.render_media._renderers  # type: ignore[union-attr]
    assert isinstance(renderers["template"], VideoRenderMedia)
    assert isinstance(renderers["generated_scene"], SceneRenderMedia)


def test_unknown_method_is_refused_not_dropped() -> None:
    """조용히 빼면 대화가 안내한 방식이 라우터에서 사라져, 확정 뒤에야 막힌다."""
    with pytest.raises(ValueError, match="모르는 제작 방식"):
        _build(methods=("template", "generated_clip"))


def test_empty_methods_is_refused() -> None:
    with pytest.raises(ValueError, match="methods"):
        _build(methods=())


# ── spec에 못박히는 값 (FR-M1 결정론) ──────────────────────────────────


def test_style_is_pinned_into_the_spec() -> None:
    """배선으로만 넘기면 같은 media_spec이 채널마다 다른 mp4를 낳는다."""
    resolve = _build(style="motion").resolve_media_spec
    assert resolve is not None
    assert resolve({"slides": []}).media_spec["style"] == "motion"


def test_three_column_is_the_empty_style_in_the_spec() -> None:
    """사람에게 `""`를 고르라고 할 수는 없다 — 표기는 3col, spec은 빈 문자열."""
    assert spec_style("3col") == ""
    assert spec_style("motion") == "motion"
    resolve = _build(style="3col").resolve_media_spec
    assert resolve is not None
    assert "style" not in resolve({"slides": []}).media_spec


def test_character_ref_is_pinned_into_the_spec(tmp_path: Path) -> None:
    anchor = tmp_path / "character.png"
    anchor.write_bytes(b"png-bytes")
    url = anchor.resolve().as_uri()
    resolve = _build(style="motion", character_image_url=url).resolve_media_spec
    assert resolve is not None
    assert resolve({"slides": []}).media_spec["character_ref"] == url


def test_missing_character_anchor_fails_loudly(tmp_path: Path) -> None:
    """조용히 캐릭터 없이 렌더하면 사람이 고른 캐릭터가 사라진 걸 아무도 모른다."""
    gone = (tmp_path / "gone.png").resolve().as_uri()
    with pytest.raises(FileNotFoundError):
        _build(character_image_url=gone)


def test_budget_is_one_per_wiring() -> None:
    """예산은 사이클 하나에 하나 — 배선을 재사용하면 이전 소비가 이어진다."""
    first, second = _build(), _build()
    assert first.resolve_media_spec is not second.resolve_media_spec


# ── 대본 단계 (렌더·과금 없이 고정값만) ────────────────────────────────


def test_script_only_resolve_pins_values_without_resolving() -> None:
    resolve = extras_only_resolve(style="motion", character_image_url="file:///c/x.png")
    spec = resolve({"slides": [{"image_query": "coffee"}]}).media_spec
    assert spec["style"] == "motion"
    assert spec["character_ref"] == "file:///c/x.png"
    # 해소를 돌리지 않았으므로 image_query가 image_ref로 바뀌지 않는다(과금 없음).
    assert spec["slides"] == [{"image_query": "coffee"}]


def test_script_only_resolve_omits_absent_values() -> None:
    spec = extras_only_resolve(style="3col", character_image_url=None)({"slides": []}).media_spec
    assert "style" not in spec
    assert "character_ref" not in spec


# ── 화면 문법 지침 (배선과 같은 곳에서 온다) ────────────────────────────


def test_motion_tells_the_agent_to_use_image_scenes() -> None:
    """모션 화면은 코드·도해 컷을 그라데이션으로 강등한다 — 애초에 쓰지 않게 유도한다."""
    guidance = style_guidance("motion")
    assert "image_query" in guidance
    assert "code" in guidance


def test_other_styles_get_no_guidance() -> None:
    """3col은 코드 컷이 제 자리다 — 쓰지 말라고 하면 그 스타일의 쓸모가 사라진다."""
    assert style_guidance("3col") == ""
    assert style_guidance("clip") == ""
