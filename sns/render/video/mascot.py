"""채널 캐릭터의 컷별 배치 — 온보딩이 박제한 마스코트를 영상에 등장시킨다.

[sns.onboarding.character]가 캐릭터를 1회 생성해 저장소에 못박아 두고 "영상 합성 반영은
후속"이라 남긴 자리다. 여기가 그 후속이다.

**움직임이 컷 성격을 읽는다.** 이 레포는 Ken Burns 줌을 걷어내면서 그 이유를
"의미 없는 움직임 대신 정보를 담는 변화"라고 적었다([sns.render.video.renderer]).
캐릭터가 그냥 흔들리면 같은 잣대에 걸린다. 그래서 자리는 `concept.kind`가 정한다 —
충격 수치엔 크게 위로, 전후 비교엔 오른쪽으로, 마무리엔 가운데로.

**컷당 정지 PNG에 합성한다.** 렌더러가 2-패스(컷 PNG → concat + 진행바)라, PNG에 그리면
ffmpeg 파이프라인이 안 바뀌고 결정론도 그대로다. 컷이 3~4초라 그 주기로 자리가 바뀌는
것이 "내용에 반응한다"로 읽힌다. 매 프레임 흔들리는 연속 모션은 오버레이 필터가 필요해
후속이다(진행바가 쓰는 `overlay=x='...t...'`가 그 수법이다).

**배치는 전부 정사각 안이다.** 쇼츠 UI가 하단 300px·우측 96px를 가리므로(06 §2 안전영역)
정사각(360~1300) 밖으로 나가면 잘린다.
"""

from dataclasses import dataclass

# 정사각 변 대비 캐릭터 변. 기본은 그림을 덮지 않을 만큼 작게, 주인공이 되는 컷만 키운다.
_SIZE_DEFAULT = 0.20
_SIZE_EMPHASIS = 0.28
_SIZE_REMEMBER = 0.30
# 정사각 안쪽 여백(변 대비) — 모서리에 딱 붙으면 잘려 보인다.
_PAD = 0.04
# emphasis가 기본 자리에서 위로 뜨는 거리(변 대비). "같이 놀란다"를 자리로 표현한다.
_HOP = 0.12


@dataclass(frozen=True)
class Placement:
    """프레임 좌표계의 캐릭터 자리. `size`는 정사각 변(캐릭터 이미지는 1:1이다)."""

    x: int
    y: int
    size: int


def _ratios(kind: str | None) -> tuple[float, float, float]:
    """(가로 정렬 0~1, 바닥에서 띄울 거리 비율, 크기 비율)."""
    if kind == "emphasis":
        return 0.0, _HOP, _SIZE_EMPHASIS  # 왼쪽에서 위로 튀어오른다
    if kind == "compare":
        return 1.0, 0.0, _SIZE_DEFAULT  # 오른쪽 — 전후 변화의 방향
    if kind == "remember":
        return 0.5, 0.0, _SIZE_REMEMBER  # 가운데로 나서는 마무리
    if kind in ("flow", "steps", "terminal"):
        return 0.0, 0.0, _SIZE_DEFAULT  # 왼쪽에서 진행을 지켜본다
    return 0.0, 0.0, _SIZE_DEFAULT


MASCOT_KINDS: frozenset[str] = frozenset(
    {"emphasis", "compare", "remember", "flow", "steps", "terminal"}
)
"""배치 규칙을 가진 개념 그림 종류. [sns.render.concept_image.CONCEPT_FIELDS]와 같아야 한다 —
그림꼴이 늘었는데 여기가 안 늘면 새 컷에서 캐릭터가 조용히 기본 자리로 간다."""


def place_mascot(kind: str | None, *, square_x: int, square_y: int, side: int) -> Placement:
    """컷 성격 → 캐릭터 자리. 모르는 kind와 `None`(개념 그림 없는 컷)은 기본 자리다."""
    align, hop, size_ratio = _ratios(kind)
    size = round(side * size_ratio)
    pad = round(side * _PAD)
    span = side - size - pad * 2  # 캐릭터가 움직일 수 있는 가로 폭
    x = square_x + pad + round(span * align)
    y = square_y + side - size - pad - round(side * hop)
    return Placement(x=x, y=y, size=size)
