"""run_profile_cycle의 순수 배선 — DB·LLM 없이 도는 부분만 본다.

스크립트라 통째로는 테스트할 수 없지만, **매핑을 틀리면 되돌릴 수 없는 사고**가 난다
(manual 채널이 자동 발행되는 것처럼). 그 매핑만 떼어 확인한다.
"""

import pytest

from scripts.run_profile_cycle import channel_mode_of


def test_manual_mode_is_not_promoted_to_auto() -> None:
    """예전 코드는 manual을 auto로 바꿔 자동 발행 경로에 넣었다."""
    assert channel_mode_of("manual") == "manual"


def test_known_modes_pass_through() -> None:
    assert channel_mode_of("hybrid") == "hybrid"
    assert channel_mode_of("auto") == "auto"


def test_unknown_mode_is_refused() -> None:
    """조용한 폴백이 있으면 오타 하나가 발행 모드를 바꾼다."""
    with pytest.raises(SystemExit):
        channel_mode_of("whatever")
