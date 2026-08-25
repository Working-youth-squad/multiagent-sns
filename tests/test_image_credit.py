"""사진 출처 표기 — Pexels API 가이드라인 준수. 순수 함수, 네트워크 0.

Pexels **License**는 크레딧을 요구하지 않지만 **API 가이드라인**은 촬영자와 Pexels
링크 표기를 요구한다. 둘을 혼동해 "크레딧 불필요"로 넘어가면 API 이용 조건 위반이다.

화면이 아니라 **캡션(설명란)** 에 붙인다 — 3단 레이아웃에 줄을 하나 더 얹는 것보다
가볍고, 발행 플랫폼 양쪽(유튜브 설명·인스타 캡션) 모두 텍스트를 받는다.
"""

from sns.render.images.credit import CREDIT_HEADING, image_credits, with_image_credits

SPEC: dict[str, object] = {
    "topic": "주제",
    "slides": [
        {"subtitle": "코드", "narration": "코드 컷.", "code": "x = 1"},
        {
            "subtitle": "사진",
            "narration": "사진 컷.",
            "image_ref": "mem://image/a.png",
            "image_source": "https://www.pexels.com/photo/42/",
            "image_credit": "Christina Morillo",
        },
    ],
}


def test_no_photos_means_no_credit_block() -> None:
    """코드·개념 그림만 쓴 영상에 빈 크레딧 제목만 남으면 캡션이 지저분해진다."""
    caption = "본문입니다.\n#파이썬"
    assert with_image_credits(caption, {"slides": [{"code": "x = 1"}]}) == caption


def test_credit_appended_after_caption() -> None:
    result = with_image_credits("본문입니다.", SPEC)
    assert result.startswith("본문입니다.")
    assert CREDIT_HEADING in result
    assert "Christina Morillo" in result
    assert "https://www.pexels.com/photo/42/" in result


def test_credits_are_deduplicated() -> None:
    """같은 사진을 두 컷에 쓰면 같은 줄이 두 번 나온다."""
    slide = SPEC["slides"]
    assert isinstance(slide, list)
    doubled = {**SPEC, "slides": [slide[1], dict(slide[1])]}
    assert len(image_credits(doubled)) == 1


def test_credits_keep_slide_order() -> None:
    """순서가 흔들리면 같은 spec이 매번 다른 캡션을 낳는다(FR-M1 정신)."""
    spec = {
        "slides": [
            {"image_source": "https://www.pexels.com/photo/2/", "image_credit": "B"},
            {"image_source": "https://www.pexels.com/photo/1/", "image_credit": "A"},
        ]
    }
    assert [c.photographer for c in image_credits(spec)] == ["B", "A"]


def test_missing_photographer_still_credits_the_source() -> None:
    """촬영자 이름이 비어 와도 출처 링크는 남겨야 한다."""
    spec = {"slides": [{"image_source": "https://www.pexels.com/photo/9/", "image_credit": ""}]}
    [credit] = image_credits(spec)
    assert credit.page_url.endswith("/9/")
    assert "https://www.pexels.com/photo/9/" in with_image_credits("본문", spec)


def test_slide_without_source_is_skipped() -> None:
    """image_ref만 있고 출처가 없으면 표기할 근거가 없다 — 지어내지 않는다."""
    assert image_credits({"slides": [{"image_ref": "mem://image/a.png"}]}) == ()


def test_malformed_spec_yields_nothing() -> None:
    assert image_credits({"slides": "nope"}) == ()
    assert image_credits({}) == ()


def test_idempotent_when_applied_twice() -> None:
    """사이클을 재실행해도 크레딧이 두 번 붙지 않아야 한다."""
    once = with_image_credits("본문입니다.", SPEC)
    assert with_image_credits(once, SPEC) == once
