"""생성 이미지 미리보기 — 모델별 화풍을 나란히 놓고 고르는 용도.

이미지 생성은 Google·OpenAI 둘 다 **유료**다(Google은 무료 티어 할당량이 0). 그래서 이
스크립트는 파이프라인 밖에 있다 — 사이클은 `generate` 미배선이 기본이고, 여기서 화풍을
확인한 뒤에 붙일지 정한다.

실행:
    uv run python scripts/preview_generated_image.py                    # 프롬프트만(무료)
    uv run python scripts/preview_generated_image.py --call             # env 모델로 생성
    uv run python scripts/preview_generated_image.py --call \
        -m google:gemini-3.1-flash-lite-image -m openai:gpt-image-1     # 모델 비교

모델을 여러 개 주면 **같은 주제를 같은 프롬프트로** 돌려 한 시트에 모아준다. 화풍 규칙이
코드에 고정돼 있어(STYLE_RULES) 비교가 화풍 차이로 오염되지 않는다.
"""

import argparse
import io
import sys
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

from sns.render.fonts import FONT_CANDIDATES, pick_font
from sns.render.images.generate import (
    ImageGenerationError,
    build_prompt,
    generate_image,
    resolve_model,
)
from sns.render.images.square import ImageSourceError, to_square

ROOT = Path(__file__).parent.parent
OUT = ROOT / "scripts" / "out" / "generated"
THUMB = 420

# 개발 주제에 실제로 붙일 법한 구도 — 개념을 말로 지시할 수 있다는 게 스톡과의 차이다.
SUBJECTS = (
    "one glowing blue cube standing apart from a long grey row of identical cubes",
    "a single arrow jumping directly to one slot, versus a chain of arrows walking a row",
    "an abstract hash table: keys on the left connected by beams to scattered buckets",
)


def slug(model: str) -> str:
    return model.replace(":", "-").replace("/", "-")


def sheet(rows: list[tuple[str, list[Image.Image | None]]], font: str) -> Image.Image:
    """행 = 모델, 열 = 주제. 빈 칸은 실패한 것이다(감추지 않는다)."""
    label = ImageFont.truetype(font, 22)
    width = len(SUBJECTS) * (THUMB + 20) + 20
    canvas = Image.new("RGB", (width, len(rows) * (THUMB + 60) + 20), (20, 22, 26))
    draw = ImageDraw.Draw(canvas)
    for r, (name, images) in enumerate(rows):
        top = 20 + r * (THUMB + 60)
        draw.text((20, top - 2), name, font=label, fill=(228, 231, 235))
        for c, image in enumerate(images):
            box = (20 + c * (THUMB + 20), top + 30)
            if image is None:
                draw.rectangle(
                    (*box, box[0] + THUMB, box[1] + THUMB), outline=(70, 40, 40), width=2
                )
                draw.text((box[0] + 14, box[1] + 14), "실패", font=label, fill=(200, 120, 120))
            else:
                canvas.paste(image.resize((THUMB, THUMB), Image.LANCZOS), box)
    return canvas


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--call", action="store_true", help="실제 생성 호출 (과금)")
    parser.add_argument(
        "-m", "--model", action="append", default=[],
        help="provider:model. 여러 번 주면 비교 시트를 만든다. 미지정 시 IMAGE_GEN_MODEL",
    )  # fmt: skip
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)

    provider, model_name = resolve_model(None)  # env 또는 기본값
    models: list[str] = args.model or [f"{provider.name}:{model_name}"]
    for i, subject in enumerate(SUBJECTS, 1):
        print(f"[{i}] {build_prompt(subject)}\n")
    if not args.call:
        print(f"대상 모델: {', '.join(models)}")
        print("실제로 만들려면 --call (결제 필요, 장당 과금)")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, list[Image.Image | None]]] = []
    failures = 0
    for model in models:
        print(f"\n── {model}")
        images: list[Image.Image | None] = []
        for i, subject in enumerate(SUBJECTS, 1):
            try:
                square = to_square(generate_image(subject, model=model))
            except (ImageGenerationError, ImageSourceError) as exc:
                print(f"   [{i}] 실패 — {exc}")
                images.append(None)
                failures += 1
                continue
            path = OUT / f"{slug(model)}-{i}.png"
            path.write_bytes(square)
            images.append(Image.open(io.BytesIO(square)).convert("RGB"))
            print(f"   [{i}] {path.name}")
        rows.append((model, images))

    font, _ = pick_font(None, FONT_CANDIDATES)
    sheet(rows, font).save(OUT / "_시트.png")
    print(f"\n{OUT.resolve()}")
    return 1 if failures == len(models) * len(SUBJECTS) else 0


if __name__ == "__main__":
    sys.exit(main())
