"""생성 이미지 미리보기 — 결제를 켠 계정에서 화풍을 눈으로 확인하는 용도.

이미지 모델은 **무료 티어 할당량이 0**이라 결제 없이는 429가 돌아온다(2026-08 실측).
그래서 이 스크립트는 파이프라인 밖에 있다 — 사이클은 `generate` 미배선이 기본이고,
여기서 화풍을 확인한 뒤에 붙일지 말지 정한다.

실행:
    uv run python scripts/preview_generated_image.py            # 프롬프트만 출력(무료)
    uv run python scripts/preview_generated_image.py --call     # 실제 생성(과금)
"""

import argparse
import io
import sys
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

from sns.render.fonts import FONT_CANDIDATES, pick_font
from sns.render.images.generate import (
    IMAGE_MODEL,
    ImageGenerationError,
    build_prompt,
    generate_image,
)
from sns.render.images.square import to_square

ROOT = Path(__file__).parent.parent
OUT = ROOT / "scripts" / "out" / "generated"

# 개발 주제에 실제로 붙일 법한 구도 — 개념을 말로 지시할 수 있다는 게 스톡과의 차이다.
SUBJECTS = (
    "one glowing blue cube standing apart from a long grey row of identical cubes",
    "a single arrow jumping directly to one slot, versus a chain of arrows walking a row",
    "an abstract hash table: keys on the left connected by beams to scattered buckets",
)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--call", action="store_true", help="실제 생성 호출 (과금)")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)

    print(f"모델: {IMAGE_MODEL}\n")
    for i, subject in enumerate(SUBJECTS, 1):
        print(f"[{i}] {build_prompt(subject)}\n")
    if not args.call:
        print("실제로 만들려면 --call (결제 필요, 장당 과금)")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    squares: list[Image.Image] = []
    for i, subject in enumerate(SUBJECTS, 1):
        try:
            square = to_square(generate_image(subject))
        except ImageGenerationError as exc:
            print(f"[{i}] 실패 — {exc}")
            continue
        (OUT / f"gen{i}.png").write_bytes(square)
        squares.append(Image.open(io.BytesIO(square)).convert("RGB"))
        print(f"[{i}] 저장 → {OUT / f'gen{i}.png'}")

    if not squares:
        return 1
    kor, _ = pick_font(None, FONT_CANDIDATES)
    label = ImageFont.truetype(kor, 24)
    sheet = Image.new("RGB", (len(squares) * 500 + 20, 560), (20, 22, 26))
    draw = ImageDraw.Draw(sheet)
    for i, img in enumerate(squares):
        sheet.paste(img.resize((480, 480), Image.LANCZOS), (10 + i * 500, 15))
        draw.text((10 + i * 500, 508), f"시안 {i + 1}", font=label, fill=(228, 231, 235))
    sheet.save(OUT / "_시트.png")
    print(f"\n{OUT.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
