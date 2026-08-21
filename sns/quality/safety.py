"""FR-Q7 텍스트 안전 검열 — 금지 소재 차단. 결정론, LLM 0.

FR-Q7은 금지어/카테고리를 **코드 상수로 외부화**하고 같은 입력이 같은 판정을 내도록
요구한다(프롬프트 판정 아님). 이미지 게이트([sns.render.images.gate])와 같은 규율의
텍스트판이고, 검사 시점은 **콘텐츠 생성 직후·렌더 전**이다.

**막는 것은 "계정이 날아가는 소재"뿐이다.** 세 가지를 구분한다:

    과장           "확 빨라집니다", "이거 모르면 손해"   → 막지 않는다. 후크의 핵심이다.
    근거 없는 수치  "생산성 300%"                       → 여기서 막지 않는다.
    금지 소재      음란·정치·비방·불법 복제              → 막는다.

수치를 정규식으로 막으면 정당한 수치("메모리 40% 감소")까지 죽는다. 오탐 비용이 커서
Content Agent 프롬프트가 다루는 게 맞다.

**사실 오류는 룰로 못 잡는다.** "Prettier가 타입 오류를 잡아준다"에는 금지어가 하나도
없다. FR-Q7이 "룰 + LLM 판정 **이중** 검사"라고 쓴 이유이고, LLM 쪽은 비결정론이라
별도 조각이다. 여기 있는 건 결정론 절반이다.

## 한국어 매칭이 영어와 다른 점

영어는 `\\b`로 단어 경계가 잡히지만 **한국어는 띄어쓰기가 단어 경계가 아니다.**
부분 문자열로 매칭하면 "정당"이 "정당한 이유"를, "대선"이 "대선배"를 잡는다. 그래서
토큰으로 쪼갠 뒤 **토큰이 금지어로 시작하는지**만 본다(조사 대응: 대통령이·대통령을).
그래도 애매한 짧은 단어는 목록에서 뺐다 — 놓치는 편이 멀쩡한 콘텐츠를 막는 것보다 낫다.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# 카테고리별 금지어. 짧고 애매한 단어는 **의도적으로 뺐다**:
#   '정당'(정당한) · '대선'(대선배) · '노출'(API 노출) · '우회'(방화벽 우회) · '최악'(최악의 경우)
#   'torrent'(BitTorrent 프로토콜은 정상 개발 주제다)
BLOCKED_TERMS: dict[str, tuple[str, ...]] = {
    "nsfw": ("음란", "야동", "성인물", "포르노", "몰카", "porn", "nsfw", "hentai"),
    # 실험 설계상 정치 편향은 금지 소재. 인물·기관을 특정하는 말만 넣는다.
    "political": ("대통령", "국회의원", "총선", "대선후보", "여당", "야당", "탄핵", "정치인"),
    # 특정인 비방은 룰로 온전히 못 잡는다. 개발 문맥에서 쓰일 일이 없는 욕설·모욕만 잡고,
    # 맥락이 필요한 판단은 LLM 검사 몫으로 남긴다.
    "abuse": ("사기꾼", "병신", "개새끼", "씨발", "멍청이", "등신"),
    "piracy": ("크랙", "키젠", "불법다운", "정품인증우회", "crack", "keygen", "nulled", "warez"),
}

# 검열 대상 텍스트 필드. `code`는 뺀다 — 변수명이 금지어를 스치는 오탐이 실익보다 크다.
_SLIDE_TEXT_FIELDS = ("subtitle", "narration")


@dataclass(frozen=True)
class Finding:
    """걸린 지점 1건 — 어디서 무엇이 왜."""

    category: str
    term: str
    where: str

    def describe(self) -> str:
        return f"{self.where}: 금지 소재({self.category}) — {self.term!r}"


def _tokens(text: str) -> list[str]:
    """공백·문장부호로 자른 토큰. 한글 조사는 토큰 뒤에 붙어 있으므로 접두로 본다."""
    return [t for t in re.split(r"[\s.,!?…·:;()\[\]{}\"'`/\\|@#*+~^=<>-]+", text.lower()) if t]


def screen_text(text: str, *, where: str) -> tuple[Finding, ...]:
    """텍스트 1건 검열 — 카테고리마다 최초로 걸린 단어 1개씩."""
    if not text.strip():
        return ()
    tokens = _tokens(text)
    findings: list[Finding] = []
    for category, terms in BLOCKED_TERMS.items():
        hit = next((t for t in terms if any(tok.startswith(t) for tok in tokens)), None)
        if hit is not None:
            findings.append(Finding(category=category, term=hit, where=where))
    return tuple(findings)


def _spec_texts(media_spec: Mapping[str, object]) -> list[tuple[str, str]]:
    """(위치, 텍스트) 목록 — 화면에 나가거나 말해지는 모든 문자열."""
    out: list[tuple[str, str]] = []
    topic = media_spec.get("topic")
    if isinstance(topic, str):
        out.append(("topic", topic))
    slides = media_spec.get("slides")
    if not isinstance(slides, list):
        return out  # malformed는 spec 파서가 따로 막는다. 여기서 죽지만 않으면 된다.
    for index, slide in enumerate(slides):
        if not isinstance(slide, Mapping):
            continue
        for field in _SLIDE_TEXT_FIELDS:
            value = slide.get(field)
            if isinstance(value, str):
                out.append((f"slides[{index}].{field}", value))
    return out


def screen_content(*, body: str, media_spec: Mapping[str, object]) -> tuple[Finding, ...]:
    """발행 캡션 + 화면·음성 텍스트 전체 검열.

    캡션만 보면 게이트가 뚫린다 — 자막과 나레이션도 그대로 나간다. 걸린 것을 **전부**
    돌려준다: 하나 고치고 다시 돌렸더니 또 걸리는 일이 없게.
    """
    findings: list[Finding] = list(screen_text(body, where="body"))
    for where, text in _spec_texts(media_spec):
        findings.extend(screen_text(text, where=where))
    return tuple(findings)


def summarize(findings: Sequence[Finding]) -> str:
    """이벤트 로그·알림에 실을 한 줄."""
    return " / ".join(f.describe() for f in findings)
