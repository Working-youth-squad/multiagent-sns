"""C3 카드 렌더 검증 — 결정론(FR-M1)·스펙 파싱·저장 seam(FR-M3)·계약 바인딩."""

import hashlib

import pytest

from sns.render.card import (
    CardRenderMedia,
    CardSpecError,
    parse_card_spec,
    render_card,
)
from sns.render.card.spec import MAX_CARD_SIDE
from sns.render.storage import InMemoryMediaStore

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
    crowded = {**VALID_SPEC, "body": [f"line {i} of a very crowded card body" for i in range(40)]}
    render = CardRenderMedia(InMemoryMediaStore()).render(crowded)
    assert render.overflow
