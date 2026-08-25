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

_SQUARE_GUIDANCE = """     * code(선택): 가운데 정사각에 문법 강조되어 그려질 코드. **최대 18줄**,
       한 줄은 짧게(48자 이내 권장 — 길면 글자가 작아져 안 읽힌다). 비우면 배경만 나온다.
       나레이션이 코드를 가리키는 컷에는 넣어라 — 말과 화면이 같은 것을 가리켜야 한다.
     * lang(선택): pygments 렉서 이름("python","javascript","sql"…). 비우면 추측한다.
     * concept(선택): 코드가 없는 컷에 **우리가 그리는 그림**. 종류 «N»개뿐이고
       컷 성격에 맞는 걸 고른다. 다른 kind나 없는 필드를 쓰면 거부된다.
       **코드가 없는 주제(도구 소개·트렌드·커리어)라면 정사각을 비우지 말고 여기서 골라라.**
       같은 kind를 연속 컷에 쓰되 active만 옮기면 화면이 진행되는 것처럼 보인다.
«EXAMPLES»
     * image_query(선택): **물리적 대상**을 말하는 컷에만 쓰는 실사 스톡 검색어
       (영어 2~4단어). 데이터센터·모니터·서버처럼 실제로 사진이 존재하는 대상일 때만
       의미가 있다. **추상 개념에는 절대 쓰지 마라** — 검색어의 단어에만 반응해
       엉뚱한 사진이 온다(전에 "list vs set" 컷에 전선 사진이 붙었다). 개념은 concept이
       맡는다. 사람·얼굴·로고·정치·무기가 들어간 검색어는 게이트에 막힌다.
     * image_prompt(선택): **코드가 한 컷도 없는 영상에서만** 쓸 수 있는 생성 이미지
       구도 설명(영어 한 문장). 커리어·트렌드·도구 소개처럼 보여줄 코드가 없는 주제가
       그 자리다. 화풍(어두운 배경·플랫 벡터·글자 없음)은 코드가 붙이니 **무엇이 어떻게
       놓여 있는지만** 써라: "a lone figure walking toward a bright doorway".
       코드를 쓰는 영상이면 한 컷이라도 image_prompt를 넣는 순간 전체가 거부된다 —
       코드 영상의 핵심 컷은 숫자와 비교라 concept이 낫기 때문이다.
     * 한 컷에는 code·concept·image_query·image_prompt 중 **하나만** 쓴다(정사각은
       하나다). 코드가 1순위, 추상 개념이면 concept, 실제 사물이면 image_query다.
       마땅치 않으면 비워라 — 억지로 붙인 그림보다 빈 배경이 낫다.
     * focus_lines(선택): 지금 말하고 있는 코드 줄 번호 목록(1-기반). 나머지 줄은
       어둡게 눌린다. code 없이 쓰면 거부된다. 같은 코드를 초점만 바꿔 연속 컷으로
       보여주면 "지금 이 줄"이 전달된다."""

DEVELOPER = Domain(
    ref="developer",
    audience="개발자 대상",
    topic_domain="개발자",
    categories=("신기술", "기초지식", "꿀팁", "현직자일상", "개발자유머"),
    grounding_prompt=(
        "한국 개발자 커뮤니티에서 최근 화제인 기술 주제 후보를 근거와 함께 한 줄씩 나열해줘. "
        "각 줄은 '- '로 시작하고, 확인되지 않은 내용은 넣지 마."
    ),
    # 네이버 검색·데이터랩에 넣을 질의어. 첫 항목이 대표 검색어다.
    search_terms=("개발자", "파이썬", "AI"),
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
    square_guidance=_SQUARE_GUIDANCE,
)
