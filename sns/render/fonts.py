"""렌더 공용 폰트 해석 — 카드·영상이 같은 규칙으로 CJK 폰트를 찾는다.

한글 글리프가 없는 폰트로 폴백하면 자산이 **두부(□)**로 렌더된 채 품질 게이트를 통과해
발행까지 갈 수 있다(FR-Q1). 그래서 못 찾으면 조용한 폴백 대신 `FontNotFoundError`로
즉시 실패한다. 영상 렌더러가 먼저 이 규칙을 세웠고, 카드도 같은 결함을 갖고 있어
여기로 합쳤다.
"""

from pathlib import Path

# (Pillow용 폰트 파일, ASS/fontconfig용 패밀리 이름) — 앞에서부터 존재하는 것 사용.
# 배포 타깃은 Linux/Docker이므로 Noto CJK를 우선한다(Dockerfile이 fonts-noto-cjk 설치).
# Windows 경로는 로컬 개발 편의용 폴백 — 프로덕션 렌더는 여기에 의존하지 않는다.
FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "Noto Sans CJK KR"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "Noto Sans CJK KR"),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "Noto Sans CJK KR"),
    (r"C:\Windows\Fonts\malgunbd.ttf", "Malgun Gothic"),
)


# 코드 렌더용 고정폭 후보. 한글 글리프가 없는 폰트(Cascadia·Consolas)가 섞여 있으므로
# 한글은 위 FONT_CANDIDATES로 폴백해 그린다([sns.render.code_image.display_runs]).
# Noto Sans Mono CJK처럼 한글까지 있는 폰트여도 같은 경로로 동작한다.
MONO_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("/usr/share/fonts/opentype/noto/NotoSansMonoCJKkr-Regular.otf", "Noto Sans Mono CJK KR"),
    ("/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Regular.ttf", "JetBrains Mono"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "DejaVu Sans Mono"),
    (r"C:\Windows\Fonts\CascadiaMono.ttf", "Cascadia Mono"),
    (r"C:\Windows\Fonts\consola.ttf", "Consolas"),
)


class FontNotFoundError(RuntimeError):
    """한글 렌더 가능한 폰트를 못 찾음 — 두부(□) 렌더로 이어지지 않도록 명시적 실패."""


def pick_font(
    font_path: str | None, candidates: tuple[tuple[str, str], ...] = FONT_CANDIDATES
) -> tuple[str, str]:
    """(Pillow 폰트 경로, ASS 패밀리 이름). 명시 경로가 오면 이름은 파일 stem."""
    if font_path is not None:
        return font_path, Path(font_path).stem
    for path, family in candidates:
        if Path(path).exists():
            return path, family
    raise FontNotFoundError(
        "한글(CJK) 폰트를 찾을 수 없습니다 — 컨테이너에 fonts-noto-cjk를 설치하거나 "
        "font_path를 명시하세요. 탐색 경로: " + ", ".join(p for p, _ in candidates)
    )
