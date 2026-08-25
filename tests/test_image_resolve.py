"""image_query → image_ref 해소 — 렌더 밖에서 한 번만 도는 단계. 네트워크 0.

이 단계의 존재 이유가 FR-M1이다. 렌더 시점에 검색하면 같은 `media_spec`이 날마다 다른
영상을 낳는다. 여기서 사진을 **못박아** 저장소에 넣고, 렌더러는 그 바이트만 읽는다.

두 번째 이유는 폴백이다. 스톡이 0건이거나 API가 죽어도 영상은 나와야 한다(13-로드맵 §5).
실패는 조용히 삼키지 않고 note로 남긴다.
"""

import hashlib
import io

import pytest
from PIL import Image

from sns.render.images.gate import StockImage
from sns.render.images.generate import ImageGenerationError
from sns.render.images.pexels import PexelsError
from sns.render.images.resolve import resolve_images
from sns.render.storage import InMemoryMediaStore

SPEC: dict[str, object] = {
    "topic": "리스트에서 in 쓰지 마세요",
    "slides": [
        {"subtitle": "코드 컷", "narration": "코드가 있는 컷.", "code": "x = 1"},
        {"subtitle": "사진 컷", "narration": "사진이 붙을 컷.", "image_query": "network cables"},
    ],
}


def candidate(**over: object) -> StockImage:
    base: dict[str, object] = {
        "source": "pexels",
        "source_id": "42",
        "page_url": "https://www.pexels.com/photo/42/",
        "download_url": "https://images.pexels.com/photos/42/l2x.jpeg",
        "width": 1600,
        "height": 1500,
        "alt": "coiled network cables",
        "photographer": "Someone",
        "license_id": "pexels",
    }
    return StockImage(**{**base, **over})  # type: ignore[arg-type]


def photo_bytes(color: tuple[int, int, int] = (30, 90, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1600, 1500), color).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def fake_search(query: str, *, limit: int = 15) -> list[StockImage]:
    return [candidate()]


def fake_download(url: str) -> bytes:
    return photo_bytes()


def test_query_becomes_ref_and_bytes_land_in_store() -> None:
    store = InMemoryMediaStore()
    result = resolve_images(SPEC, store=store, search=fake_search, download=fake_download)
    slides = result.media_spec["slides"]
    assert isinstance(slides, list)
    ref = slides[1]["image_ref"]
    assert ref.startswith("mem://image/")
    stored = store.blobs[ref]
    assert Image.open(io.BytesIO(stored)).size == (940, 940)
    assert hashlib.sha256(stored).hexdigest()[:16] in ref


def test_input_spec_is_not_mutated() -> None:
    store = InMemoryMediaStore()
    resolve_images(SPEC, store=store, search=fake_search, download=fake_download)
    slides = SPEC["slides"]
    assert isinstance(slides, list)
    assert "image_ref" not in slides[1]


def test_code_slide_is_left_alone() -> None:
    store = InMemoryMediaStore()
    result = resolve_images(SPEC, store=store, search=fake_search, download=fake_download)
    slides = result.media_spec["slides"]
    assert isinstance(slides, list)
    assert "image_ref" not in slides[0]


def test_provenance_recorded_for_audit() -> None:
    """저작권 근거는 사후에 확인할 수 있어야 한다 — 출처 페이지를 spec에 남긴다."""
    store = InMemoryMediaStore()
    result = resolve_images(SPEC, store=store, search=fake_search, download=fake_download)
    slides = result.media_spec["slides"]
    assert isinstance(slides, list)
    assert slides[1]["image_source"] == "https://www.pexels.com/photo/42/"
    assert slides[1]["image_credit"] == "Someone"  # 캡션 크레딧 줄의 재료


def test_deterministic_for_same_photo_bytes() -> None:
    a = resolve_images(SPEC, store=InMemoryMediaStore(), search=fake_search, download=fake_download)
    b = resolve_images(SPEC, store=InMemoryMediaStore(), search=fake_search, download=fake_download)
    assert a.media_spec == b.media_spec


def test_no_candidate_falls_back_without_ref() -> None:
    """스톡이 0건이면 그라데이션으로 간다 — 영상 자체가 죽으면 안 된다."""
    result = resolve_images(
        SPEC, store=InMemoryMediaStore(), search=lambda q, limit=15: [], download=fake_download
    )
    slides = result.media_spec["slides"]
    assert isinstance(slides, list)
    assert "image_ref" not in slides[1]
    assert any("후보" in n for n in result.notes)


def test_search_failure_is_noted_not_raised() -> None:
    def boom(query: str, *, limit: int = 15) -> list[StockImage]:
        raise PexelsError("429 Too Many Requests")

    result = resolve_images(SPEC, store=InMemoryMediaStore(), search=boom, download=fake_download)
    assert any("429" in n for n in result.notes)


def test_unusable_downloaded_bytes_are_noted_not_raised() -> None:
    result = resolve_images(
        SPEC,
        store=InMemoryMediaStore(),
        search=fake_search,
        download=lambda url: b"<html>404</html>",
    )
    slides = result.media_spec["slides"]
    assert isinstance(slides, list)
    assert "image_ref" not in slides[1]
    assert any("디코드" in n for n in result.notes)


def test_blocked_query_is_noted_not_raised() -> None:
    spec = {
        **SPEC,
        "slides": [{"subtitle": "부제", "narration": "한 문장.", "image_query": "nude portrait"}],
    }
    result = resolve_images(
        spec, store=InMemoryMediaStore(), search=fake_search, download=fake_download
    )
    assert any("금지 소재" in n for n in result.notes)


def test_already_resolved_ref_is_left_alone() -> None:
    """재실행이 멱등이어야 한다 — 같은 사이클을 다시 돌려도 사진이 바뀌지 않는다."""
    called: list[str] = []

    def counting(query: str, *, limit: int = 15) -> list[StockImage]:
        called.append(query)
        return [candidate()]

    spec = {
        **SPEC,
        "slides": [
            {
                "subtitle": "부제",
                "narration": "한 문장.",
                "image_query": "network cables",
                "image_ref": "mem://image/pinned.png",
            }
        ],
    }
    result = resolve_images(
        spec, store=InMemoryMediaStore(), search=counting, download=fake_download
    )
    slides = result.media_spec["slides"]
    assert isinstance(slides, list)
    assert slides[0]["image_ref"] == "mem://image/pinned.png"
    assert called == []


def test_malformed_spec_raises() -> None:
    with pytest.raises(ValueError, match="slides"):
        resolve_images(
            {"topic": "x"}, store=InMemoryMediaStore(), search=fake_search, download=fake_download
        )


# ── 생성 이미지 (기본 미배선) ─────────────────────────────────────

PROMPT_SPEC: dict[str, object] = {
    "topic": "주제",
    "slides": [
        {
            "subtitle": "생성 컷",
            "narration": "그림이 붙을 컷.",
            "image_prompt": "one glowing cube apart from a grey row",
        }
    ],
}


def test_image_prompt_without_generator_is_noted_not_silently_dropped() -> None:
    """생성은 유료라 기본 미배선이다 — 그림이 왜 안 붙었는지는 남아야 한다."""
    result = resolve_images(
        PROMPT_SPEC, store=InMemoryMediaStore(), search=fake_search, download=fake_download
    )
    slides = result.media_spec["slides"]
    assert isinstance(slides, list)
    assert "image_ref" not in slides[0]
    assert any("generate" in n for n in result.notes)


def test_generated_bytes_land_in_the_store() -> None:
    result = resolve_images(
        PROMPT_SPEC,
        store=(store := InMemoryMediaStore()),
        search=fake_search,
        download=fake_download,
        generate=lambda subject: photo_bytes((10, 60, 180)),
    )
    slides = result.media_spec["slides"]
    assert isinstance(slides, list)
    ref = slides[0]["image_ref"]
    assert Image.open(io.BytesIO(store.blobs[ref])).size == (940, 940)


def test_generated_image_records_no_stock_credit() -> None:
    """우리가 만든 그림에 Pexels 출처를 달면 거짓 표기가 된다."""
    result = resolve_images(
        PROMPT_SPEC,
        store=InMemoryMediaStore(),
        search=fake_search,
        download=fake_download,
        generate=lambda subject: photo_bytes(),
    )
    slides = result.media_spec["slides"]
    assert isinstance(slides, list)
    assert "image_source" not in slides[0] and "image_credit" not in slides[0]


def test_generation_failure_falls_back_with_a_note() -> None:
    def boom(subject: str) -> bytes:
        raise ImageGenerationError("429 결제 필요")

    result = resolve_images(
        PROMPT_SPEC,
        store=InMemoryMediaStore(),
        search=fake_search,
        download=fake_download,
        generate=boom,
    )
    slides = result.media_spec["slides"]
    assert isinstance(slides, list)
    assert "image_ref" not in slides[0]
    assert any("결제" in n for n in result.notes)


def test_generation_wins_over_stock_when_both_given() -> None:
    """직접 말한 구도가 검색어보다 정확하다 — 둘 다 오면 생성을 쓴다."""
    both = {
        **PROMPT_SPEC,
        "slides": [{**PROMPT_SPEC["slides"][0], "image_query": "network cables"}],  # type: ignore[index]
    }
    searched: list[str] = []

    def counting(query: str, *, limit: int = 15) -> list[StockImage]:
        searched.append(query)
        return [candidate()]

    resolve_images(
        both,
        store=InMemoryMediaStore(),
        search=counting,
        download=fake_download,
        generate=lambda subject: photo_bytes(),
    )
    assert searched == [], "생성이 있는데 스톡을 검색함"
