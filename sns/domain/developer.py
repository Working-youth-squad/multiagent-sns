"""개발자 도메인 팩 — 이 프로젝트가 처음부터 다뤄온 주제 범위.

값은 전부 기존 하드코딩에서 **그대로** 옮겨온 것이다(프롬프트 문구·카테고리 5종·
그라운딩 질의·소스 구성·개념 그림 예시). 이관 시점에 문구를 고치지 않았다 — 리팩터가
동작을 바꾸지 않았다는 걸 기존 테스트로 증명하기 위해서다.

카테고리 5종의 근거는 04 §4.1이고, 개념 그림 예시가 개발 향인 이유는 그 컷들이 실제
발행분에서 나온 것이기 때문이다("list vs set", "O(n) -> O(1)").
"""

from sns.domain.pack import Domain

_CONCEPT_EXAMPLES = {
    "emphasis": (
        "       - 충격적인 수치·키워드 한 방:\n"
        '         {"kind":"emphasis","tag":"최악의 경우","headline":"100억",'
        '"sub":"십만 건 × 십만 건 비교"}\n'
        "         headline은 한글 8자 이내. 숫자 하나면 가장 세다."
    ),
    "compare": (
        "       - 느린 방법 vs 빠른 방법 도해(왜 빨라지는지 보여주는 컷):\n"
        '         {"kind":"compare","before_label":"list","before_note":"6번 비교",\n'
        '          "after_label":"set","after_note":"1번 비교","footer":"O(n) -> O(1)"}\n'
        "         label은 짧은 이름(한글 8자 이내), footer는 전후 변화 한 줄."
    ),
    "remember": (
        '       - 마무리 "기억하세요" 한 줄:\n'
        '         {"kind":"remember","line":"반복문 안에서 in을 쓴다면","code":"set(...)"}\n'
        "         line은 한글 17자 이내, code는 기억할 짧은 코드(선택)."
    ),
    "flow": (
        "       - 동작 원리·파이프라인(무엇이 어떤 순서로 일어나는지):\n"
        '         {"kind":"flow","steps":["주제 한 줄 입력","AI가 대본 작성",'
        '"영상·음성 합성"],"active":1}\n'
        "         steps는 **최대 3개**, 각 한글 10자 이내. active(0-기반)는 지금 말하는 단계."
    ),
    "steps": (
        "       - 기능·항목 나열:\n"
        '         {"kind":"steps","items":["대본 자동 생성","영상 자동 합성",'
        '"자막·TTS 자동"],"active":2}\n'
        "         items는 **최대 4개**, 각 한글 12자 이내. active는 지금 말하는 항목."
    ),
    "terminal": (
        "       - 설치·시작 명령(도구 소개의 마무리):\n"
        '         {"kind":"terminal","commands":["pip install foo"],"note":"깃허브에서 무료"}\n'
        "         commands는 **최대 2줄**, 각 34자 이내. note는 한 줄 설명(선택)."
    ),
}

DEVELOPER = Domain(
    ref="developer",
    audience="개발자 대상",
    topic_domain="개발자",
    categories=("신기술", "기초지식", "꿀팁", "현직자일상", "개발자유머"),
    grounding_prompt=(
        "한국 개발자 커뮤니티에서 최근 화제인 기술 주제 후보를 근거와 함께 한 줄씩 나열해줘. "
        "각 줄은 '- '로 시작하고, 확인되지 않은 내용은 넣지 마."
    ),
    trend_sources=(
        "google_trends",
        "github_trending",
        "hacker_news",
        "lobsters",
        "naver_search",
        "naver_datalab",
        "youtube_popular",
        "llm_grounding",
    ),
    concept_kinds=("emphasis", "compare", "remember", "flow", "steps", "terminal"),
    concept_examples=_CONCEPT_EXAMPLES,
)
