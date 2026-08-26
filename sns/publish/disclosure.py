"""AI 생성 콘텐츠 표기 — 발행 본문에 붙는 한 줄.

06 §5가 생성형 영상을 스코프 아웃한 사유 셋 중 **(c) 콘텐츠 정책(비진정성) 리스크**에
대한 답이다. 스펙 §11의 두 층 중 이쪽(캡션)을 담당한다 — 나머지 한 층인 플랫폼 공개
플래그는 어댑터 몫이고, 필드명 확인 전에는 캡션만으로 실 발행하지 않는다.

**표기는 코드가 붙인다 — LLM이 아니다.** Content 에이전트가 캡션에 써주기를 기대하면
안 쓰는 날이 오고, 그날 표기 없는 합성 영상이 자동 발행된다. 사람이 안 보는 경로다.

**`template`에는 붙이지 않는다.** 코드·개념 그림·스톡 사진은 우리가 그리거나 라이선스를
확인한 것이라 합성 콘텐츠가 아니다. 전부에 붙이면 표기가 의미를 잃는다.

[sns.render.images.credit.with_image_credits]와 같은 모양이다(본문 + media_spec → 본문) —
러너가 두 함수를 나란히 부른다.
"""

from collections.abc import Mapping

AI_DISCLOSURE = "※ 이 영상의 장면은 AI로 생성했습니다."

# 생성 이미지를 쓰는 제작 방식. hybrid는 컷을 따로 본다 — 선언만으로는 알 수 없다.
_GENERATED_METHODS = frozenset({"generated_scene", "generated_clip"})


def needs_disclosure(media_spec: Mapping[str, object]) -> bool:
    """이 영상이 합성 장면을 쓰는가."""
    method = str(media_spec.get("method", "template"))
    if method in _GENERATED_METHODS:
        return True
    if method != "hybrid":
        return False
    # hybrid는 컷마다 방식이 다르다 — 하나라도 생성이면 붙인다.
    slides = media_spec.get("slides")
    if not isinstance(slides, list):
        return False
    return any(
        isinstance(slide, Mapping) and str(slide.get("method", "template")) in _GENERATED_METHODS
        for slide in slides
    )


def with_ai_disclosure(body: str, media_spec: Mapping[str, object]) -> str:
    """필요하면 발행 본문 끝에 표기를 붙인다. 이미 있으면 그대로 둔다(재실행 멱등)."""
    if not needs_disclosure(media_spec) or AI_DISCLOSURE in body:
        return body
    return f"{body.rstrip()}\n\n{AI_DISCLOSURE}"
