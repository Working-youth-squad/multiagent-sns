"""생성 이미지 예산 — 유료 호출 직전에 센다 (FR-P6).

컷 상한이 60장(`MAX_SLIDES`)이라 그것만 믿으면 한 사이클이 60번 과금될 수 있다.
초과는 **폴백이 아니라 사고**이므로 조용히 그라데이션으로 떨어뜨리지 않고 던진다 —
FR-P6가 요구하는 "초과 시 발행 스킵 + 알림"이 그 뜻이다.
"""

import pytest

from sns.render.video.gen.budget import (
    MAX_GENERATED_IMAGES_PER_CYCLE,
    BudgetExceeded,
    ImageBudget,
)


def test_spends_up_to_the_limit() -> None:
    budget = ImageBudget(limit=3)
    for _ in range(3):
        budget.spend()
    assert budget.spent == 3


def test_raises_past_the_limit() -> None:
    budget = ImageBudget(limit=2)
    budget.spend()
    budget.spend()
    with pytest.raises(BudgetExceeded, match="2"):
        budget.spend()


def test_default_limit_is_the_documented_constant() -> None:
    assert MAX_GENERATED_IMAGES_PER_CYCLE == 12
    assert ImageBudget().spent == 0


def test_zero_limit_refuses_immediately() -> None:
    """예산 0은 '생성 끄기'다 — 첫 호출부터 막는다."""
    with pytest.raises(BudgetExceeded):
        ImageBudget(limit=0).spend()


def test_failed_spend_does_not_count() -> None:
    """막힌 호출은 돈을 안 썼으므로 세지 않는다 — 사유 보고가 어긋나지 않게."""
    budget = ImageBudget(limit=1)
    budget.spend()
    with pytest.raises(BudgetExceeded):
        budget.spend()
    assert budget.spent == 1
