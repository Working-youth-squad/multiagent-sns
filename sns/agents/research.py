"""주제 리서치 — 확정된 주제로 웹 검색 그라운딩 1회, '근거 노트'를 만든다.

콘텐츠 에이전트의 알맹이 없음은 입력 문제였다: 받는 것이 트렌드 헤드라인 한 줄과
지어낸 요약 한 줄뿐이라, "출처 없는 숫자 금지" 규칙 아래에서 쓸 수 있는 구체성이
0이 된다. 여기서 만든 근거 노트가 [sns.agents.content.run_content]의 `research`로
들어가면 에이전트는 확인된 사실·수치를 인용할 수 있다.

배선은 seam이다([sns.runner.cycle]의 `research_topic`) — 미주입이면 사이클은 기존
그대로 돈다. 리서치 실패도 사이클을 죽이지 않는다(notice 기록 후 근거 없이 진행).
"""

from sns.net.http import DEFAULT_OPENER, Opener
from sns.research.sources.llm_grounding import GEMINI_URL, fetch_grounded_text

# 검색 그라운딩은 텍스트 생성보다 느리다 — 트렌드 소스(10s)보다 넉넉히.
TIMEOUT_S = 30.0

_PROMPT = (
    "주제: {topic}\n"
    "이 주제로 짧은 정보성 영상 대본을 쓰려고 한다. 웹 검색으로 확인한 사실만으로 "
    "근거 노트를 만들어라: 시청자가 몰랐을 구체적 사실·수치·온도·시간·순서·흔한 실수 "
    "5~8개를 '- '로 시작하는 한 줄씩 쓰고, 각 줄 끝에 (출처: 도메인)을 붙여라. "
    "확인되지 않은 내용과 일반론은 넣지 마라."
)


def research_topic(
    topic_title: str,
    *,
    api_key: str,
    url: str = GEMINI_URL,
    timeout_s: float = TIMEOUT_S,
    opener: Opener = DEFAULT_OPENER,
) -> str:
    """주제 → 근거 노트 텍스트. 비면 근거 없음(호출자가 없는 것으로 취급)."""
    return fetch_grounded_text(
        _PROMPT.format(topic=topic_title.strip()),
        api_key=api_key,
        url=url,
        timeout_s=timeout_s,
        opener=opener,
    ).strip()
