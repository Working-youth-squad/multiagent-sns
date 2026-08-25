"""원본 사진 → 940 정사각 PNG. 결정론(FR-M1) + 신뢰할 수 없는 바이트 방어.

여기 들어오는 바이트는 **제3자 서버에서 온 것**이라, 카드·코드 이미지와 달리
"입력이 이미지이긴 한가"부터 의심해야 한다. 디코드 폭탄·비이미지·과소 해상도를
전부 예외로 끊는다.
"""

import io

import pytest
from PIL import Image

from sns.render.images.square import (
    MAX_SOURCE_PIXELS,
    MIN_SOURCE_SIDE,
    ImageSourceError,
    to_square,
)


def photo(width: int, height: int, *, color: tuple[int, int, int] = (200, 80, 40)) -> bytes:
    img = Image.new("RGB", (width, height), color)
    # 균일색이면 크롭 위치를 못 본다 — 왼쪽 절반만 다른 색으로 칠해 기준점을 만든다.
    img.paste(Image.new("RGB", (width // 2, height), (20, 40, 200)), (0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def opened(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def test_output_is_square_png_at_requested_size() -> None:
    out = to_square(photo(1600, 900), size=940)
    img = opened(out)
    assert img.size == (940, 940)
    assert out[:8] == b"\x89PNG\r\n\x1a\n"


def test_deterministic() -> None:
    src = photo(1600, 900)
    assert to_square(src) == to_square(src)


def test_portrait_source_also_becomes_square() -> None:
    assert opened(to_square(photo(900, 1600))).size == (940, 940)


def test_crop_is_centered() -> None:
    """가로 사진은 좌우를 같은 만큼 잘라야 한다 — 한쪽으로 쏠리면 피사체가 날아간다."""
    img = opened(to_square(photo(2000, 1000)))
    # 원본은 왼쪽 절반이 파랑. 중앙 크롭이면 결과의 왼쪽 끝은 여전히 파랑 영역 안이다.
    left = img.getpixel((30, 470))
    right = img.getpixel((910, 470))
    assert left[2] > left[0], f"왼쪽이 파랑이 아님: {left}"
    assert right[0] > right[2], f"오른쪽이 주황이 아님: {right}"


def test_upscales_small_but_acceptable_source() -> None:
    assert opened(to_square(photo(MIN_SOURCE_SIDE, MIN_SOURCE_SIDE))).size == (940, 940)


def test_too_small_source_rejected() -> None:
    """작은 원본을 940까지 늘리면 뭉개져서 화면 가운데가 흐려진다."""
    with pytest.raises(ImageSourceError, match="해상도"):
        to_square(photo(MIN_SOURCE_SIDE - 1, 1200))


def test_non_image_bytes_rejected() -> None:
    with pytest.raises(ImageSourceError, match="이미지"):
        to_square(b"<html>404 Not Found</html>")


def test_decompression_bomb_rejected() -> None:
    """픽셀 수 상한 — 원격 바이트가 메모리를 통째로 먹지 못하게."""
    side = int(MAX_SOURCE_PIXELS**0.5) + 500
    header = Image.new("RGB", (1, 1))
    buf = io.BytesIO()
    header.save(buf, format="PNG")
    # 실제로 거대한 이미지를 만들지 않고, 선언된 크기만 큰 상황을 흉내낸다.
    huge = Image.new("L", (side, side))
    big = io.BytesIO()
    huge.save(big, format="PNG", compress_level=1)
    with pytest.raises(ImageSourceError, match="픽셀"):
        to_square(big.getvalue())


def test_dim_darkens_for_background_use() -> None:
    """자막·주제가 위에 얹히는 컷에서는 사진을 눌러야 글자가 읽힌다."""
    bright = opened(to_square(photo(1600, 900), dim=0.0)).getpixel((470, 470))
    dark = opened(to_square(photo(1600, 900), dim=0.55)).getpixel((470, 470))
    assert sum(dark) < sum(bright) * 0.6


def test_dim_out_of_range_rejected() -> None:
    with pytest.raises(ImageSourceError, match="dim"):
        to_square(photo(1600, 900), dim=1.5)
