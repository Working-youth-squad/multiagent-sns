"""goal 프리셋 레지스트리 (FR-E · cycle.goal_ref, T0-5).

`cycle.goal_ref`는 여기 등록된 키 중 하나. 4개 프리셋은 13-로드맵 §4의
미결정 항목 "조회수/팔로워/저장/시청유지 우선순위"에 1:1 대응한다.

동결 범위: 프리셋 키·의미·1차 신호(metric_key, 11-데이터모델 §4)만 여기서 고정한다.
RewardFn 계수는 M1 실측 후 사전등록(FR-L2)이므로 이 모듈은 계수를 담지 않는다.

⚠️ 실험 기본 goal(DEFAULT_GOAL_REF) 선택은 Phase 0 선결 공동 결정 — 아래 값은
   제안 기본값이며 팀 확정 시 갱신한다.
"""

from dataclasses import dataclass
from typing import Literal

GoalRef = Literal["reach_growth", "follower_growth", "engagement_depth", "watch_through"]


@dataclass(frozen=True)
class GoalPreset:
    ref: GoalRef
    label: str
    # 이 goal이 우선 정렬하는 공식 신호 (11-데이터모델 §4의 metric_key)
    ig_signals: tuple[str, ...]
    yt_signals: tuple[str, ...]
    description: str


GOAL_PRESETS: dict[GoalRef, GoalPreset] = {
    "reach_growth": GoalPreset(
        ref="reach_growth",
        label="조회·도달 성장",
        ig_signals=("views", "reach"),
        yt_signals=("views",),
        description="도달/재생 확대 우선. heavy-tail 방어로 조회수는 log 보조(FR-L2).",
    ),
    "follower_growth": GoalPreset(
        ref="follower_growth",
        # IG 팔로워 증가는 계정 단위(follower_count) — per-post metric 표준 밖, 별도 관측
        label="팔로워·구독 성장",
        ig_signals=("saved", "shares"),  # 팔로우 전환의 per-post 선행 근사
        yt_signals=("subscribers_gained",),
        description="구독/팔로워 전환 우선. YT=subscribers_gained, IG=계정 팔로워 증가(별도).",
    ),
    "engagement_depth": GoalPreset(
        ref="engagement_depth",
        label="참여 깊이(저장·공유)",
        ig_signals=("saved", "shares", "likes", "comments"),
        yt_signals=("likes", "comments", "shares"),
        description="저장·공유 등 능동 참여 우선 — 알고리즘 추천 신호와 정렬(FR-L2).",
    ),
    "watch_through": GoalPreset(
        ref="watch_through",
        label="시청 유지·완주",
        ig_signals=("avg_watch_time_ms", "skip_rate"),
        yt_signals=("avg_view_pct", "engaged_views", "views"),
        description="완주 근사(avg_watch_time÷길이)·engagedViews÷views 우선.",
    ),
}

# ⚠️ Phase 0 선결 공동 결정 대상 — 제안 기본값
DEFAULT_GOAL_REF: GoalRef = "engagement_depth"


def resolve_goal(goal_ref: str) -> GoalPreset:
    """goal_ref를 프리셋으로 해석. 미등록 키는 거부(오타·잘못된 cycle 차단)."""
    for ref, preset in GOAL_PRESETS.items():
        if ref == goal_ref:
            return preset
    valid = ", ".join(sorted(GOAL_PRESETS))
    raise ValueError(f"알 수 없는 goal_ref: {goal_ref!r} (등록: {valid})")
