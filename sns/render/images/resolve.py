"""`image_query` → `image_ref` 해소 — 생성 시점 1회, 렌더 밖.

렌더 시점에 검색하면 같은 `media_spec`이 날마다 다른 영상을 낳아 FR-M1이 깨진다.
여기서 사진을 골라 940 정사각으로 굽고 저장소에 못박은 뒤, spec의 질의를 저장소 URL로
바꾼다. 렌더러가 읽는 건 그 URL뿐이라 렌더는 네트워크 없이 결정론으로 돈다.

**실패는 영상을 죽이지 않는다.** 스톡이 0건이거나 API가 429를 주면 그 컷은 질의를 그대로
둔 채 그라데이션으로 간다(13-로드맵 §5 폴백 규칙). 다만 조용히 삼키지 않고 `notes`로
올린다 — 사진이 안 붙은 이유를 나중에 물어볼 수 있어야 한다.
"""

import copy
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast

from sns.render.images.gate import StockImage, pick_image, screen_query
from sns.render.images.generate import ImageGenerationError
from sns.render.images.pexels import DEFAULT_LIMIT, PexelsError, download_image, search_pexels
from sns.render.images.square import ImageSourceError, to_square
from sns.render.storage import MediaStore

# 사진 위에 글자가 얹히진 않지만(3단 레이아웃은 밴드가 분리돼 있다), 원색 사진은 코드
# 이미지 옆에서 혼자 튄다. 살짝 눌러 톤을 맞춘다.
DEFAULT_DIM = 0.25

SearchImages = Callable[..., list[StockImage]]
DownloadImage = Callable[[str], bytes]
# 생성은 **유료 전용**이라 기본 미배선이다. 켜려면 명시로 주입한다:
#     resolve_images(spec, store=store, generate=generate_image)
# 기본값을 `generate_image`로 두면 결제가 켜진 계정에서 사이클이 조용히 돈을 쓴다.
GenerateImage = Callable[[str], bytes]


@dataclass(frozen=True)
class ImageResolution:
    """해소된 `media_spec`과, 사진이 안 붙은 컷의 사유."""

    media_spec: dict[str, object]
    notes: tuple[str, ...] = ()


def _resolve_slide(
    slide: dict[str, object],
    where: str,
    *,
    store: MediaStore,
    search: SearchImages,
    download: DownloadImage,
    generate: GenerateImage | None,
    dim: float,
) -> str | None:
    """사진을 붙였으면 None, 못 붙였으면 사유 문자열."""
    if slide.get("image_ref"):
        return None  # 이미 못박혀 있다(재실행 멱등)

    # 생성이 스톡보다 앞이다 — 직접 말한 구도가 검색어보다 정확하다.
    prompt = str(slide.get("image_prompt", "")).strip()
    if prompt:
        if generate is None:
            return f"{where} 'image_prompt'가 있으나 generate가 미배선(유료) — 그림 생략"
        verdict = screen_query(prompt)
        if not verdict.allowed:
            return f"{where} {verdict.reason}"
        try:
            square = to_square(generate(prompt), dim=dim)
        except (ImageGenerationError, ImageSourceError, OSError) as exc:
            return f"{where} 이미지 생성 실패 — {exc}"
        # 우리가 만든 그림이라 출처 표기가 없다 — Pexels 크레딧을 달면 거짓 표기가 된다.
        slide["image_ref"] = store.put(
            square, checksum=hashlib.sha256(square).hexdigest(), kind="image", ext="png"
        )
        return None

    query = str(slide.get("image_query", "")).strip()
    if not query:
        return None  # 붙일 게 없다
    # 게이트를 여기서도 건다. `search_pexels`도 검열하지만 그건 어댑터 사정이라,
    # 소스를 갈아끼우면 게이트가 통째로 빠진다 — 금지 소재 차단이 주입 대상이면 안 된다.
    verdict = screen_query(query)
    if not verdict.allowed:
        return f"{where} {verdict.reason}"
    try:
        picked = pick_image(search(query, limit=DEFAULT_LIMIT))
        if picked is None:
            return f"{where} 게이트를 통과한 후보 없음 — 질의 {query!r}"
        square = to_square(download(picked.download_url), dim=dim)
    except (PexelsError, ImageSourceError, OSError) as exc:
        return f"{where} 사진 해소 실패 — {exc}"

    checksum = hashlib.sha256(square).hexdigest()
    slide["image_ref"] = store.put(square, checksum=checksum, kind="image", ext="png")
    # 출처는 spec(jsonb)에 남긴다 — 저작권 근거를 사후에 확인할 수 있어야 하고(FR-Q7),
    # 캡션의 크레딧 줄도 여기서 나온다([sns.render.images.credit]). 렌더 입력이 아니라
    # 감사·표기 메타라 `parse_video_spec`은 이 키들을 읽지 않는다.
    slide["image_source"] = picked.page_url
    slide["image_credit"] = picked.photographer
    return None


def resolve_images(
    media_spec: Mapping[str, object],
    *,
    store: MediaStore,
    search: SearchImages = search_pexels,
    download: DownloadImage = download_image,
    generate: GenerateImage | None = None,
    dim: float = DEFAULT_DIM,
) -> ImageResolution:
    """`media_spec`의 모든 `image_query`를 해소한 **새 spec**을 돌려준다(입력 불변)."""
    slides = media_spec.get("slides")
    if not isinstance(slides, list):
        raise ValueError(f"'slides'는 리스트여야 함: {slides!r}")

    # 손대는 건 슬라이드뿐이라 최상위는 얕게, 슬라이드만 깊게 복사한다(입력 불변).
    resolved_slides: list[object] = copy.deepcopy(slides)
    notes: list[str] = []
    for index, slide in enumerate(resolved_slides):
        if not isinstance(slide, dict):
            raise ValueError(f"'slides[{index}]'는 객체여야 함: {slide!r}")
        note = _resolve_slide(
            cast(dict[str, object], slide),
            f"slides[{index}]:",
            store=store,
            search=search,
            download=download,
            generate=generate,
            dim=dim,
        )
        if note:
            notes.append(note)
    return ImageResolution({**media_spec, "slides": resolved_slides}, tuple(notes))
