"""C3 카드 렌더 검증 — 결정론(FR-M1)·스펙 파싱·저장 seam(FR-M3)·계약 바인딩."""

import hashlib
from dataclasses import replace

import pytest

from sns.render.card import (
    CardRenderMedia,
    CardSpecError,
    parse_card_spec,
    render_card,
)
from sns.render.card import renderer as card_renderer
from sns.render.card.spec import (
    MAX_BODY_PARAGRAPHS,
    MAX_BODY_WIDTH,
    MAX_CARD_SIDE,
    MAX_FOOTER_WIDTH,
    MAX_HOOK_WIDTH,
    MAX_TITLE_WIDTH,
)
from sns.render.fonts import FontNotFoundError
from sns.render.storage import InMemoryMediaStore
from sns.render.text import display_width

VALID_SPEC: dict[str, object] = {
    "hook": "You are shipping bugs in your sleep",
    "title": "3 Postgres indexes every backend dev misses",
    "body": [
        "Partial indexes cut write cost on wide tables.",
        "Covering indexes skip the heap fetch entirely.",
        "BRIN wins on append-only time-series data.",
    ],
    "footer": "Save this for your next migration",
}


def test_parse_fills_defaults() -> None:
    spec = parse_card_spec(VALID_SPEC)
    assert spec.width == 1080 and spec.height == 1350
    assert spec.palette.background == "#0d1117"  # 기본 팔레트
    assert spec.body[0].startswith("Partial")


def test_parse_normalizes_single_string_body() -> None:
    spec = parse_card_spec({**VALID_SPEC, "body": "one paragraph"})
    assert spec.body == ("one paragraph",)


@pytest.mark.parametrize(
    "bad",
    [
        {**VALID_SPEC, "title": ""},  # 빈 필수 필드
        {k: v for k, v in VALID_SPEC.items() if k != "hook"},  # 누락
        {**VALID_SPEC, "body": []},  # 빈 body
        {**VALID_SPEC, "body": [1, 2]},  # 비문자열 body
        {**VALID_SPEC, "palette": {"background": "0d1117"}},  # # 없는 hex
        {**VALID_SPEC, "palette": {"foreground": "#zzz"}},  # 잘못된 hex
        {**VALID_SPEC, "width": 0},  # 비양수 치수
        {**VALID_SPEC, "width": True},  # bool은 int 아님
        {**VALID_SPEC, "width": 100_000_000},  # 상한 초과 → 메모리 폭탄 방어
        {**VALID_SPEC, "height": MAX_CARD_SIDE + 1},  # 상한 바로 위
    ],
)
def test_parse_rejects_malformed(bad: dict[str, object]) -> None:
    with pytest.raises(CardSpecError):
        parse_card_spec(bad)


def test_parse_accepts_dimension_at_upper_bound() -> None:
    # 상한 경계값은 허용 — 상한 초과만 차단.
    spec = parse_card_spec({**VALID_SPEC, "width": MAX_CARD_SIDE, "height": MAX_CARD_SIDE})
    assert spec.width == MAX_CARD_SIDE and spec.height == MAX_CARD_SIDE


def test_render_is_deterministic() -> None:
    # FR-M1: 같은 spec → 같은 바이트 → 같은 checksum.
    spec = parse_card_spec(VALID_SPEC)
    first, second = render_card(spec), render_card(spec)
    assert first.png == second.png
    assert not first.overflow


def test_render_media_same_spec_same_checksum() -> None:
    render = CardRenderMedia(InMemoryMediaStore())
    a = render(VALID_SPEC, "image")
    b = render(VALID_SPEC, "image")
    assert a == b
    assert a.checksum == hashlib.sha256(render.render(VALID_SPEC).png).hexdigest()
    assert a.storage_url == f"mem://image/{a.checksum}.png"


def test_render_media_different_spec_different_checksum() -> None:
    render = CardRenderMedia(InMemoryMediaStore())
    a = render(VALID_SPEC, "image")
    b = render({**VALID_SPEC, "title": "A different title entirely"}, "image")
    assert a.checksum != b.checksum


def test_store_persists_bytes_content_addressed() -> None:
    store = InMemoryMediaStore()
    asset = CardRenderMedia(store)(VALID_SPEC, "image")
    assert store.blobs[asset.storage_url].startswith(b"\x89PNG")


def test_render_media_rejects_video_kind() -> None:
    render = CardRenderMedia(InMemoryMediaStore())
    with pytest.raises(ValueError, match="kind"):
        render(VALID_SPEC, "video")


def test_overflow_flag_set_when_text_exceeds_safe_area() -> None:
    # 파싱 용량 상한을 우회해 **렌더러의 overflow 감지 자체**를 검증한다. 상한 안이라도
    # 폰트가 더 넓거나 안전영역이 달라지면 넘칠 수 있어, 이 방어선은 게이트에 남는다.
    crowded = replace(
        parse_card_spec(VALID_SPEC),
        body=tuple(f"line {i} of a very crowded card body" for i in range(40)),
    )
    assert render_card(crowded).overflow


# ── 용량 상한 (FR-Q1) — 렌더 후 overflow가 아니라 파싱에서 막는다 ──
# 글자수가 아니라 **표시 폭**으로 잰다: 한글 1자 = 폭 2, 라틴 1자 = 폭 1.
# 실측 경계(맑은고딕·내장폰트 공통)는 한글 기준 훅 32 / 제목 24 / 본문 60×3 / 푸터 24자.

_KO = "가"  # 폭 2


def test_display_width_counts_cjk_double() -> None:
    assert display_width("가나다") == 6
    assert display_width("abcdef") == 6


def test_spec_at_capacity_parses() -> None:
    spec = parse_card_spec(
        {
            "hook": _KO * (MAX_HOOK_WIDTH // 2),
            "title": _KO * (MAX_TITLE_WIDTH // 2),
            "body": [_KO * (MAX_BODY_WIDTH // 2)] * MAX_BODY_PARAGRAPHS,
            "footer": _KO * (MAX_FOOTER_WIDTH // 2),
        }
    )
    assert len(spec.body) == MAX_BODY_PARAGRAPHS


def test_hook_over_capacity_rejected() -> None:
    with pytest.raises(CardSpecError, match="hook"):
        parse_card_spec({**VALID_SPEC, "hook": _KO * (MAX_HOOK_WIDTH // 2 + 1)})


def test_body_paragraph_over_capacity_rejected() -> None:
    with pytest.raises(CardSpecError, match="body"):
        parse_card_spec({**VALID_SPEC, "body": [_KO * (MAX_BODY_WIDTH // 2 + 1)]})


def test_too_many_body_paragraphs_rejected() -> None:
    with pytest.raises(CardSpecError, match="body"):
        parse_card_spec({**VALID_SPEC, "body": ["짧은 단락"] * (MAX_BODY_PARAGRAPHS + 1)})


def test_capacity_limits_prevent_render_overflow() -> None:
    """상한을 꽉 채운 스펙은 실제 렌더에서도 넘치지 않아야 한다 — 상한값의 근거."""
    spec = parse_card_spec(
        {
            "hook": _KO * (MAX_HOOK_WIDTH // 2),
            "title": _KO * (MAX_TITLE_WIDTH // 2),
            "body": [_KO * (MAX_BODY_WIDTH // 2)] * MAX_BODY_PARAGRAPHS,
            "footer": _KO * (MAX_FOOTER_WIDTH // 2),
        }
    )
    assert not render_card(spec).overflow


def test_missing_cjk_font_raises_instead_of_tofu(monkeypatch: pytest.MonkeyPatch) -> None:
    """CJK 폰트가 없으면 내장 폰트로 조용히 폴백(=한글 두부)하지 않고 실패한다.

    영상 렌더러가 먼저 세운 규칙(FontNotFoundError)을 카드도 따른다 — 깨진 자산이
    품질 게이트를 통과해 발행되는 경로를 카드 쪽에도 막는다(FR-Q1).
    """
    monkeypatch.setattr(card_renderer, "_FONT_CANDIDATES", ())
    with pytest.raises(FontNotFoundError):
        render_card(parse_card_spec(VALID_SPEC))
