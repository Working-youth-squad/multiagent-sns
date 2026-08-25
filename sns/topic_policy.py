"""주제 대분류(`topic_major`) → 파이프라인 정책 파생.

온보딩 인터뷰가 고른 주제 대분류 하나로 카테고리·개념 그림 종류·정사각 소스·프롬프트
안내가 갈린다. 값은 코드에, 분기는 문자열 하나로.

**팩(닫힌 등록제)을 대신한다.** 인터뷰는 대분류의 "직접 입력"을 허용하므로
([sns.onboarding.profile.TOPIC_MAJORS]), 등록되지 않은 주제에서 사이클이 멈추면 안 된다.
여기 모든 함수는 **모르지만 유효한** 주제를 거부하지 않고 범용 정책으로 받는다. 팩은
`resolve_domain`이 모르는 ref를 거부해서 자유 입력 주제에서 아예 돌지 않았다.

**유효성 검증은 하지 않는다.** 빈 문자열·공백 같은 무효값은
[sns.onboarding.profile.parse_profile]이 `ProfileError`로 막는다. 정본 파서가 있는데
여기서 또 검사하면 진실이 둘이 된다.

**의존이 없어야 한다.** 렌더(`sns.render`)와 에이전트(`sns.agents`)가 둘 다 이 모듈을
읽는다. 온보딩 쪽에 두면 렌더가 온보딩에 의존하게 되어 방향이 거꾸로 선다.

개발 쪽 값은 옛 도메인 팩에서 **한 글자도 고치지 않고** 옮겼다 — 이사가 동작을 바꾸지
않았음을 기존 테스트로 증명하기 위해서다.
"""

from collections.abc import Mapping
from types import MappingProxyType

DEV_MAJOR = "개발"

# 주제 카테고리 5종 (04 §4.1 · content_item.topic_category).
DEV_CATEGORIES: tuple[str, ...] = ("신기술", "기초지식", "꿀팁", "현직자일상", "개발자유머")
GENERIC_CATEGORIES: tuple[str, ...] = ("트렌드", "기초지식", "꿀팁", "일상", "유머")

# 개념 그림 종류. terminal(설치 명령)은 개발 전용이라 범용에서 뺀다.
DEV_CONCEPT_KINDS: tuple[str, ...] = (
    "emphasis",
    "compare",
    "remember",
    "flow",
    "steps",
    "terminal",
)
GENERIC_CONCEPT_KINDS: tuple[str, ...] = ("emphasis", "compare", "remember", "flow", "steps")

# 정사각을 채울 소스와 **우선순위**. 렌더러가 이 순서대로 첫 번째로 채워지는 것을 쓴다
# ([sns.render.video.renderer]). 코드가 1순위인 건 우리가 그린 것이라 저작권·네트워크
# 리스크가 없고 개발 콘텐츠의 핵심 컷이 대개 코드이기 때문이다. 코드가 없는 주제엔
# 그 소스 자체가 없다.
DEV_SQUARE_SOURCES: tuple[str, ...] = ("code", "concept", "image", "gradient")
GENERIC_SQUARE_SOURCES: tuple[str, ...] = ("concept", "image", "gradient")

_DEV_EXAMPLES: Mapping[str, str] = MappingProxyType(
    {
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
)

_GENERIC_EXAMPLES: Mapping[str, str] = MappingProxyType(
    {
        "emphasis": (
            "       - 충격적인 수치·키워드 한 방:\n"
            '         {"kind":"emphasis","tag":"실온 보관","headline":"3일",'
            '"sub":"그 뒤엔 맛이 무너진다"}\n'
            "         headline은 한글 8자 이내. 숫자 하나면 가장 세다."
        ),
        "compare": (
            "       - 흔한 방법 vs 나은 방법 도해(왜 나아지는지 보여주는 컷):\n"
            '         {"kind":"compare","before_label":"찬물","before_note":"20분",\n'
            '          "after_label":"끓는 물","after_note":"3분","footer":"20분 -> 3분"}\n'
            "         label은 짧은 이름(한글 8자 이내), footer는 전후 변화 한 줄."
        ),
        "remember": (
            '       - 마무리 "기억하세요" 한 줄:\n'
            '         {"kind":"remember","line":"반죽이 손에 붙는다면"}\n'
            "         line은 한글 17자 이내."
        ),
        "flow": (
            "       - 순서·과정(무엇이 어떤 순서로 일어나는지):\n"
            '         {"kind":"flow","steps":["재료 손질","중불에 볶기",'
            '"10분 졸이기"],"active":1}\n'
            "         steps는 **최대 3개**, 각 한글 10자 이내. active(0-기반)는 지금 말하는 단계."
        ),
        "steps": (
            "       - 요령·항목 나열:\n"
            '         {"kind":"steps","items":["기름은 적게","불은 세게","한 번만 뒤집기"],'
            '"active":2}\n'
            "         items는 **최대 4개**, 각 한글 12자 이내. active는 지금 말하는 항목."
        ),
    }
)

_DEV_SQUARE_GUIDANCE = """\
     * code(선택): 가운데 정사각에 문법 강조되어 그려질 코드. **최대 18줄**,
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

_GENERIC_SQUARE_GUIDANCE = """\
     * concept(선택): 가운데 정사각에 **우리가 그리는 그림**. 종류 «N»개뿐이고
       컷 성격에 맞는 걸 고른다. 다른 kind나 없는 필드를 쓰면 거부된다.
       **정사각을 비우지 말고 여기서 골라라.** 같은 kind를 연속 컷에 쓰되 active만
       옮기면 화면이 진행되는 것처럼 보인다.
«EXAMPLES»
     * image_query(선택): **물리적 대상**을 말하는 컷에만 쓰는 실사 스톡 검색어
       (영어 2~4단어). 실제로 사진이 존재하는 대상일 때만 의미가 있다.
       **추상 개념에는 절대 쓰지 마라** — 검색어의 단어에만 반응해 엉뚱한 사진이 온다.
       사람·얼굴·로고·정치·무기가 들어간 검색어는 게이트에 막힌다.
     * 한 컷에는 concept·image_query 중 **하나만** 쓴다(정사각은 하나다).
       마땅치 않으면 비워라 — 억지로 붙인 그림보다 빈 배경이 낫다."""


def categories_for(topic_major: str) -> tuple[str, ...]:
    """Topic 에이전트가 고를 카테고리. 성과 분석의 축이라 함부로 늘리지 않는다."""
    return DEV_CATEGORIES if topic_major == DEV_MAJOR else GENERIC_CATEGORIES


def concept_kinds_for(topic_major: str) -> tuple[str, ...]:
    """이 주제가 쓸 개념 그림 종류. [sns.render.concept_image]의 이름과 같아야 한다."""
    return DEV_CONCEPT_KINDS if topic_major == DEV_MAJOR else GENERIC_CONCEPT_KINDS


def square_sources_for(topic_major: str) -> tuple[str, ...]:
    """정사각을 채울 소스와 우선순위. 목록에 없는 소스의 슬라이드 필드는 파서가 거부한다.

    프롬프트에서 안내를 빼는 것만으로는 부족하다 — 에이전트가 안내를 무시하고 넣으면
    요리 영상에 파이썬 코드가 렌더된다.
    """
    return DEV_SQUARE_SOURCES if topic_major == DEV_MAJOR else GENERIC_SQUARE_SOURCES


def concept_examples_for(topic_major: str) -> Mapping[str, str]:
    """프롬프트에 넣을 예시 블록. **read-only** — 바꾸면 이후 모든 프롬프트가 오염된다.

    `Mapping` 힌트만으로는 런타임에 못 막는다(캐스팅하면 그만이다). code-owned
    configuration이므로 `MappingProxyType`으로 실제로 불변이게 둔다.
    """
    return _DEV_EXAMPLES if topic_major == DEV_MAJOR else _GENERIC_EXAMPLES


def square_guidance_for(topic_major: str) -> str:
    """Content 프롬프트의 정사각 섹션. «N»과 «EXAMPLES»는 호출부가 치환한다."""
    return _DEV_SQUARE_GUIDANCE if topic_major == DEV_MAJOR else _GENERIC_SQUARE_GUIDANCE


def subject_label_for(topic_major: str) -> str:
    """프롬프트의 «DOMAIN» 자리를 채우는 주제 표기. 팩의 `topic_domain`이 하던 일이다."""
    return "개발자" if topic_major == DEV_MAJOR else topic_major
