"""원본 사진 바이트 → 940 정사각 PNG (FR-M1 결정론).

코드 이미지([sns.render.code_image])와 같은 슬롯을 채우므로 테두리·크기를 맞춘다.
다른 점은 **입력을 믿을 수 없다는 것**이다. 여기 오는 바이트는 제3자 서버가 준 것이라
비이미지·디코드 폭탄·과소 해상도를 전부 예외로 끊는다 — Pillow에 그대로 넘기면
`Image.open`이 수 GB를 할당하거나 두부 같은 결과가 조용히 렌더까지 흘러간다.
"""

import io

from PIL import Image, ImageDraw, UnidentifiedImageError

from sns.render.square import DEFAULT_SIZE, EDGE

# 940까지 늘려도 뭉개지지 않는 최소 변. Pexels 원본은 보통 이보다 훨씬 크고,
# 이 밑으로 내려가면 화면 한가운데가 흐릿해져 오히려 그라데이션만 못하다.
MIN_SOURCE_SIDE = 640
# 원격 바이트의 메모리 상한. Pillow 기본(MAX_IMAGE_PIXELS)은 경고만 내고 통과시킨다.
MAX_SOURCE_PIXELS = 40_000_000
_EDGE_WIDTH = 3


class ImageSourceError(ValueError):
    """원본 이미지가 쓸 수 없는 상태 — 렌더 진입 전 차단."""


def _decode(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        width, height = img.size  # open은 지연 로딩이라 여기까진 헤더만 읽는다
        if width * height > MAX_SOURCE_PIXELS:
            raise ImageSourceError(
                f"원본 픽셀 수가 상한을 넘음 — {width}×{height} > {MAX_SOURCE_PIXELS:,}"
            )
        img.load()
    except ImageSourceError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageSourceError(f"이미지로 디코드할 수 없음: {exc}") from exc
    return img.convert("RGB")


def to_square(data: bytes, *, size: int = DEFAULT_SIZE, dim: float = 0.0) -> bytes:
    """사진 → 정사각 PNG 바이트. 같은 입력 → 같은 바이트.

    가운데를 기준으로 잘라(cover) 정사각을 만든다. `dim`(0~1)은 사진을 검정 쪽으로
    섞는 비율 — 위아래 밴드의 글자와 대비를 확보할 때 쓴다.
    """
    if not 0.0 <= dim < 1.0:
        raise ImageSourceError(f"dim은 0 이상 1 미만이어야 함: {dim}")
    img = _decode(data)
    width, height = img.size
    if min(width, height) < MIN_SOURCE_SIDE:
        raise ImageSourceError(
            f"원본 해상도가 낮음 — 짧은 변 {MIN_SOURCE_SIDE}px 이상 필요: {width}×{height}"
        )

    side = min(width, height)
    left, top = (width - side) // 2, (height - side) // 2
    square = img.crop((left, top, left + side, top + side)).resize(
        (size, size), Image.Resampling.LANCZOS
    )
    if dim > 0.0:
        square = Image.blend(square, Image.new("RGB", (size, size), (0, 0, 0)), dim)

    # 코드 이미지와 같은 테두리 — 두 소스가 같은 슬롯에서 같은 도형으로 보이게.
    ImageDraw.Draw(square).rectangle((0, 0, size - 1, size - 1), outline=EDGE, width=_EDGE_WIDTH)

    buf = io.BytesIO()
    square.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()
