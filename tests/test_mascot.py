"""캐릭터 배치 규칙 — 컷 성격에 따라 위치·크기가 바뀐다.

이 레포는 Ken Burns 줌을 **의미 없는 움직임**이라는 이유로 걷어냈다
([sns.render.video.renderer] 독스트링). 캐릭터 모션도 같은 잣대를 받는다 — 컷마다
자리가 바뀌되 그 변화가 컷 성격을 읽어야 한다. 그래서 배치는 `concept.kind`가 정한다.

컷당 정지 PNG에 합성하므로 프레임 파이프라인은 그대로다. 연속적인 흔들림은 오버레이
필터가 필요해 후속이다.
"""

import pytest

from sns.render.concept_image import CONCEPT_FIELDS
from sns.render.video.mascot import MASCOT_KINDS, Placement, place_mascot

# 1080×1920 기준 실측 좌표 — 정사각은 (70, 360)에서 940변.
SQ_X, SQ_Y, SIDE = 70, 360, 940


def _place(kind: str | None) -> Placement:
    return place_mascot(kind, square_x=SQ_X, square_y=SQ_Y, side=SIDE)


@pytest.mark.parametrize("kind", sorted(CONCEPT_FIELDS))
def test_every_concept_kind_has_a_placement(kind: str) -> None:
    """그림꼴이 늘었는데 배치가 없으면 캐릭터가 조용히 기본 자리로 간다."""
    assert kind in MASCOT_KINDS, f"{kind}: 배치 규칙 없음"


@pytest.mark.parametrize("kind", [*sorted(CONCEPT_FIELDS), None])
def test_placement_stays_inside_the_square(kind: str | None) -> None:
    """정사각 밖으로 나가면 쇼츠 UI 가림 영역(하단 300·우측 96)과 겹친다."""
    p = _place(kind)
    assert SQ_X <= p.x and p.x + p.size <= SQ_X + SIDE, f"{kind}: 가로 이탈"
    assert SQ_Y <= p.y and p.y + p.size <= SQ_Y + SIDE, f"{kind}: 세로 이탈"


@pytest.mark.parametrize("kind", [*sorted(CONCEPT_FIELDS), None])
def test_size_is_a_sane_fraction(kind: str | None) -> None:
    """너무 크면 그림을 덮고 너무 작으면 안 보인다."""
    p = _place(kind)
    assert SIDE * 0.15 <= p.size <= SIDE * 0.35, f"{kind}: 크기 {p.size}"


def test_compare_moves_right_of_flow() -> None:
    """전후 비교 컷에서는 오른쪽 — 변화의 방향을 가리킨다."""
    assert _place("compare").x > _place("flow").x


def test_emphasis_is_bigger_and_higher_than_default() -> None:
    """충격 수치 컷에서 캐릭터가 같이 반응한다."""
    emphasis, default = _place("emphasis"), _place(None)
    assert emphasis.size > default.size
    assert emphasis.y < default.y  # 위로 (y가 작을수록 위)


def test_remember_is_centered() -> None:
    """마무리 컷에서는 가운데로 나선다."""
    p = _place("remember")
    center = SQ_X + SIDE // 2
    assert abs((p.x + p.size // 2) - center) <= 2


def test_unknown_kind_falls_back_to_default() -> None:
    """개념 그림이 없는 컷(사진·빈 배경)도 캐릭터는 나온다."""
    assert _place("존재하지않는kind") == _place(None)


def test_placement_is_deterministic() -> None:
    """같은 입력 → 같은 좌표. 렌더 결정론(FR-M1)이 여기에도 걸린다."""
    assert _place("emphasis") == _place("emphasis")
