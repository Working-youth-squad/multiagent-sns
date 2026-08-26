"""포맷 선택 → `ContentFormat` 매핑 — 진입점 둘이 같은 표를 읽는다.

사람이 고르는 값은 "카드냐 영상이냐"(`FormatChoice`)지 `ContentFormat`이 아니다.
릴스냐 쇼츠냐는 **채널 플랫폼이 정한다** — 사람에게 물을 것이 아니다. 챗봇은 여러
채널을 한 사이클에 태우므로 그 갈림이 대상마다 따로 일어난다.

이 표를 진입점마다 복제하면 플랫폼이 늘 때 한쪽만 고쳐진다 — 조용히 틀린 포맷이
나가고, 그 사고는 렌더가 끝난 뒤에야 드러난다.

**여기 함수는 예외를 던지지 않는다.** `None`을 돌려주고 판정은 호출부가 한다 —
CLI는 `SystemExit`이 맞지만 웹 서버의 백그라운드 스레드에서는 그게 스레드를 조용히
죽인다(`SystemExit`은 `Exception`이 아니라 `except Exception`에 안 걸린다).
"""

from typing import Literal, get_args

from sns.tools.contracts import ContentFormat, Platform

FormatChoice = Literal["card", "video"]
"""사람이 고르는 포맷. `ContentFormat`과 다르다 — 플랫폼 갈림이 아직 안 일어난 값이다."""

FORMAT_CHOICES: tuple[FormatChoice, ...] = get_args(FormatChoice)

VIDEO_FORMAT: dict[Platform, ContentFormat] = {"instagram": "reels", "youtube": "shorts"}

PLATFORMS: dict[str, Platform] = {"instagram": "instagram", "youtube": "youtube"}


def parse_platform(value: str) -> Platform | None:
    """채널 플랫폼 문자열 → `Platform`. 모르는 값은 `None` — 조용한 폴백을 두지 않는다."""
    return PLATFORMS.get(value)


def parse_format_choice(value: str) -> FormatChoice | None:
    """사용자·LLM이 준 문자열 → `FormatChoice`. 모르는 값은 `None`."""
    for choice in FORMAT_CHOICES:
        if value == choice:
            return choice
    return None


def content_format_for(platform: Platform, choice: FormatChoice) -> ContentFormat:
    """플랫폼 × 선택 → `ContentFormat`.

    `platform`은 `parse_platform`을 통과한 값이라 여기서 다시 검증하지 않는다.
    """
    return "feed_image" if choice == "card" else VIDEO_FORMAT[platform]
