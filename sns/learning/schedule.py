"""폴링 창 스케줄 (FR-L1·A3) — 순수·결정론, API도 DB도 모른다.

창 인덱스는 **관측의 이름표**다: 0=발행 후 6h, 1=24h, 2=72h, 3+=그 뒤 일 1회
(`docs/plan/08-지표-학습.md` FR-L1). 시드 테스트→단계 확산이라는 플랫폼 공식 구조상
초기 곡선이 조기 판정 신호이므로(FR-A3), 앞의 세 창이 촘촘하고 뒤가 성기다.

## 늦은 창은 찍지 않는다

폴러가 하루 멈췄다가 살아나면 6h·24h 창이 한꺼번에 "기한 지남"이 된다. 그때 지금의
누적치를 그 창들에 나눠 적으면 **없던 곡선이 만들어진다** — 세 창이 같은 값을 갖고,
스코어보드는 "초기 6시간에 이미 다 나온 영상"으로 읽는다. 쿼터도 창 수만큼 태운다.

그래서 창마다 **유예**를 둔다(`grace_hours`: 다음 창까지 거리의 절반). 유예를 넘긴 창은
`missed`로 분류해 폴링하지 않고, 호출부가 그 사실을 `run_event`에 남긴다. 결측을 0으로
채우지 않는 것과 같은 규율이다 — **없는 관측을 만들지 않는다.**

지평(`horizon_days`)은 반대쪽 끝이다. 발행 후 그만큼 지나면 곡선이 평평해 조기 판정에
쓸모가 없고, 일 1회 폴링만 영원히 남는다.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

# 앞의 세 창(FR-L1 확정). 이후는 DAILY_STEP_H 간격으로 이어진다.
EARLY_OFFSETS_H: tuple[int, ...] = (6, 24, 72)
DAILY_STEP_H = 24
# 발행 후 이 기간까지만 폴링한다. 팀 결정 대상 — 제안값(docs/handoff 참조).
DEFAULT_HORIZON_DAYS = 14
# 유예의 하한 — 아주 촘촘한 창에서도 폴러가 한 번쯤 놓치는 것은 정상이다.
MIN_GRACE_H = 3


def window_offset_hours(index: int) -> int:
    """창 인덱스 → 발행 후 경과 시간(시간). 0=6h · 1=24h · 2=72h · 3+=일 1회."""
    if index < 0:
        raise ValueError(f"창 인덱스는 0 이상: {index}")
    if index < len(EARLY_OFFSETS_H):
        return EARLY_OFFSETS_H[index]
    return EARLY_OFFSETS_H[-1] + DAILY_STEP_H * (index - len(EARLY_OFFSETS_H) + 1)


def window_grace_hours(index: int) -> int:
    """그 창을 얼마나 늦게까지 찍어도 되는가 — 다음 창까지 거리의 절반(최소 3h).

    절반인 이유: 유예가 다음 창을 침범하면 두 창이 같은 시점을 가리키게 된다.
    """
    span = window_offset_hours(index + 1) - window_offset_hours(index)
    return max(MIN_GRACE_H, span // 2)


def window_due_at(published_at: datetime, index: int) -> datetime:
    return published_at + timedelta(hours=window_offset_hours(index))


@dataclass(frozen=True)
class WindowPlan:
    """한 발행 건에 대한 이번 실행의 계획.

    `missed`가 따로 있는 이유: 조용히 건너뛰면 인사이트 탭에서 구멍의 이유를 알 수 없다.
    "폴러가 안 돌았다"와 "API가 값을 안 줬다"는 다른 사건이고, 다른 조치를 부른다.
    """

    due: tuple[int, ...] = ()
    missed: tuple[int, ...] = ()


def plan_windows(
    *,
    published_at: datetime,
    now: datetime,
    observed: tuple[int, ...] = (),
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> WindowPlan:
    """이번 실행에서 찍을 창과, 늦어서 포기하는 창.

    이미 찍힌 창(`observed`)은 어느 쪽에도 넣지 않는다 — 그 창은 끝난 일이다.
    """
    horizon_h = horizon_days * 24
    elapsed = (now - published_at).total_seconds() / 3600
    if elapsed < 0:  # 시계 뒤틀림·미래 발행 — 아무것도 하지 않는다.
        return WindowPlan()

    due: list[int] = []
    missed: list[int] = []
    index = 0
    while True:
        offset = window_offset_hours(index)
        if offset > horizon_h:
            break
        if offset > elapsed:  # 아직 오지 않은 창 — 다음 실행에서 본다.
            break
        if index not in observed:
            (due if elapsed <= offset + window_grace_hours(index) else missed).append(index)
        index += 1
    return WindowPlan(due=tuple(due), missed=tuple(missed))
