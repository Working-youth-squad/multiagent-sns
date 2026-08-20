"""사진 출처 표기 — Pexels API 가이드라인 준수.

Pexels **License**는 크레딧을 요구하지 않는다. 하지만 **API 가이드라인**은 촬영자와
Pexels 링크 표기를 요구한다 — 둘은 별개이고, 라이선스만 보고 "크레딧 불필요"로 넘어가면
API 이용 조건 위반이다.

표기는 화면이 아니라 **캡션**에 붙인다. 3단 레이아웃에 줄을 하나 더 얹으면 자막 영역이
좁아지는데, 발행 플랫폼은 양쪽 다(유튜브 설명·인스타 캡션) 텍스트를 받으므로 그쪽이
가볍다. CC-BY처럼 화면 표기가 필수인 라이선스를 안 쓰기로 한 이유이기도 하다.

출처 정보는 [sns.render.images.resolve]가 spec에 남긴다(`image_source`·`image_credit`).
"""

from collections.abc import Mapping
from dataclasses import dataclass

CREDIT_HEADING = "사진 제공 · Pexels"


@dataclass(frozen=True)
class ImageCredit:
    photographer: str
    page_url: str

    def line(self) -> str:
        return f"{self.photographer} — {self.page_url}" if self.photographer else self.page_url


def image_credits(media_spec: Mapping[str, object]) -> tuple[ImageCredit, ...]:
    """spec에 실린 사진 출처 — 슬라이드 순서 유지, 중복 제거.

    순서를 유지하는 건 결정론 때문이다(FR-M1 정신). 집합으로 모으면 같은 spec이 매번
    다른 캡션을 낳는다. 출처가 없는 슬라이드는 건너뛴다 — 표기할 근거를 지어내지 않는다.
    """
    slides = media_spec.get("slides")
    if not isinstance(slides, list):
        return ()
    credits: dict[str, ImageCredit] = {}  # dict는 삽입 순서를 지킨다
    for slide in slides:
        if not isinstance(slide, Mapping):
            continue
        page_url = str(slide.get("image_source", "")).strip()
        if not page_url or page_url in credits:
            continue
        credits[page_url] = ImageCredit(
            photographer=str(slide.get("image_credit", "")).strip(), page_url=page_url
        )
    return tuple(credits.values())


def with_image_credits(caption: str, media_spec: Mapping[str, object]) -> str:
    """캡션 뒤에 출처 블록을 붙인다. 사진이 없으면 캡션 그대로.

    이미 붙어 있으면 다시 붙이지 않는다 — 사이클 재실행이 크레딧을 겹쳐 쌓으면 안 된다.
    """
    credits = image_credits(media_spec)
    if not credits or CREDIT_HEADING in caption:
        return caption
    lines = "\n".join(credit.line() for credit in credits)
    return f"{caption.rstrip()}\n\n{CREDIT_HEADING}\n{lines}"
