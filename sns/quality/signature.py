"""콘텐츠 지문·근접중복 판정 — 카드·영상 공통 (FR-A2 near-duplicate 방어, NFR-11).

**포맷 하나가 아니라 `media_spec` 하나를 본다.** 예전엔 카드 전용 지문만 있었고 영상
게이트에는 유사도 검사 자체가 없었다. 그래서 어제와 거의 같은 영상이 두 번 나갔다.
포맷별로 따로 만들면 한쪽만 배선되는 그 사고가 다시 난다.

지문에서 **코드는 뺀다.** 판단 대상은 "무엇을 말했나"이지 "어떤 스니펫을 띄웠나"가
아니다. 코드를 넣으면 같은 대본에 스니펫만 바꾼 재탕이 통과한다.
"""

from collections.abc import Mapping

# 직전 N건 대비 유사도 상한. 이 위면 사실상 같은 콘텐츠로 본다.
MAX_CONTENT_SIMILARITY = 0.8

# 영상 슬라이드에서 지문에 넣을 텍스트 필드(코드 제외).
_SLIDE_TEXT_FIELDS = ("subtitle", "narration")
# 카드 spec의 단일 텍스트 필드.
_CARD_TEXT_FIELDS = ("hook", "title", "footer")


def spec_texts(media_spec: Mapping[str, object]) -> list[tuple[str, str]]:
    """(위치, 텍스트) 목록 — 화면에 나가거나 말해지는 모든 문자열.

    위치를 함께 돌려주는 건 안전 검열([sns.quality.safety])이 "어디를 고쳐야 하는지"를
    사람에게 알려줘야 하기 때문이다. 유사도 쪽은 텍스트만 쓴다.

    malformed spec은 여기서 죽지 않는다 — 형식 검증은 spec 파서 몫이고, 게이트가 죽으면
    검열 없이 통과하는 것과 같다.
    """
    out: list[tuple[str, str]] = []
    for field in ("topic", *_CARD_TEXT_FIELDS):
        value = media_spec.get(field)
        if isinstance(value, str):
            out.append((field, value))

    body = media_spec.get("body")  # 카드 본문(단락 리스트)
    if isinstance(body, list):
        out.extend((f"body[{i}]", p) for i, p in enumerate(body) if isinstance(p, str))

    slides = media_spec.get("slides")  # 영상 컷
    if isinstance(slides, list):
        for index, slide in enumerate(slides):
            if not isinstance(slide, Mapping):
                continue
            out.extend(
                (f"slides[{index}].{field}", value)
                for field in _SLIDE_TEXT_FIELDS
                if isinstance(value := slide.get(field), str)
            )
    return out


def spec_signature(media_spec: Mapping[str, object]) -> frozenset[str]:
    """콘텐츠 지문 — 텍스트 토큰 집합. 팔레트·규격은 항상 같으므로 보지 않는다."""
    text = " ".join(t for _, t in spec_texts(media_spec)).lower()
    return frozenset(text.split())


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def max_similarity(signature: frozenset[str], recent: tuple[frozenset[str], ...]) -> float:
    """직전 N건 중 가장 비슷한 것과의 유사도. 이력이 없으면 0."""
    return max((jaccard(signature, r) for r in recent), default=0.0)
