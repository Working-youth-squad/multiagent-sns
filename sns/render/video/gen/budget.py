"""생성 이미지 예산 (FR-P6) — 유료 호출을 세는 단 하나의 자리.

**세는 시점이 렌더가 아니라 해소다.** 렌더 시점에는 이미 돈을 쓴 뒤라 늦다.

**초과는 폴백이 아니라 사고다.** 사진 해소는 실패해도 그라데이션으로 가지만, 예산 초과를
그렇게 처리하면 "왜 그림이 없지"만 남고 돈이 새는 줄 모른다. 던져서 사이클을 끊는다 —
FR-P6가 요구하는 "초과 시 발행 스킵 + 알림"이 그 뜻이다.

이건 Cost Gate("이만큼 써도 되는가")이고, Capability Gate("이 환경이 할 수 있는가")는
라우터가 따로 답한다([sns.render.video.router]). 둘을 뭉치면 라우터 등록이 무제한 지출
승인처럼 읽힌다.
"""

# 실측상 영상은 4~8컷이라 여유가 있고, 폭주하면 눈에 띄는 수치다. 컷 상한 자체는
# 60장([sns.render.video.spec.MAX_SLIDES])이라 그것만 믿으면 한 사이클이 60번 과금될 수 있다.
MAX_GENERATED_IMAGES_PER_CYCLE = 12


class BudgetExceeded(RuntimeError):
    """사이클 생성 예산 초과 — 그 사이클을 끊는다."""


class ImageBudget:
    """사이클 1회의 생성 호출 예산. 해소가 컷마다 `spend()`를 부른다.

    상태를 들고 있어야 하므로 함수가 아니라 객체다 — 사이클 하나에 하나를 만들어
    해소에 넘긴다. 재사용하면 이전 사이클의 소비가 이어져 계산이 어긋난다.
    """

    def __init__(self, limit: int = MAX_GENERATED_IMAGES_PER_CYCLE) -> None:
        self._limit = limit
        self._spent = 0

    @property
    def spent(self) -> int:
        """실제로 쓴 호출 수. 막힌 호출은 돈을 안 썼으므로 세지 않는다."""
        return self._spent

    def spend(self) -> None:
        """유료 호출 1회를 예약한다. 남은 예산이 없으면 `BudgetExceeded`."""
        if self._spent >= self._limit:
            raise BudgetExceeded(
                f"사이클 생성 이미지 상한 {self._limit}장 초과 — 발행을 건너뛴다(FR-P6)"
            )
        self._spent += 1
