"""코드 스니펫 → 정사각 PNG (3단 레이아웃의 가운데 칸).

개발 콘텐츠인데 화면에 코드가 없다는 게 이 채널의 최대 약점이었다. "코드 한 글자 차이"를
말로만 하지 말고 그 한 글자를 보여준다. 저작권·비용·결정론 리스크가 전부 0인 이미지 소스다.
"""

import hashlib

import pytest

from sns.render.code_image import (
    MAX_CODE_LINES,
    CodeImageError,
    display_runs,
    render_code_square,
)


def test_same_code_same_bytes() -> None:
    """FR-M1 결정론 — 같은 스니펫이면 언제나 같은 PNG."""
    code = "items = set(load_ids())\nif target in items:\n    handle(target)"
    a = render_code_square(code, size=480)
    b = render_code_square(code, size=480)
    assert hashlib.sha256(a).digest() == hashlib.sha256(b).digest()


def test_different_code_different_bytes() -> None:
    a = render_code_square("x = 1", size=480)
    b = render_code_square("x = 2", size=480)
    assert a != b


def test_output_is_square_png() -> None:
    from io import BytesIO

    from PIL import Image

    img = Image.open(BytesIO(render_code_square("x = 1", size=480)))
    assert img.size == (480, 480)
    assert img.format == "PNG"


def test_too_many_lines_rejected() -> None:
    """정사각에 안 들어가는 코드는 렌더가 아니라 **입력 단계**에서 끊는다."""
    with pytest.raises(CodeImageError, match="줄"):
        render_code_square("\n".join(f"line_{i} = {i}" for i in range(MAX_CODE_LINES + 1)))


def test_empty_code_rejected() -> None:
    with pytest.raises(CodeImageError):
        render_code_square("   \n  \n")


# ── 한글 폰트 폴백 ────────────────────────────────────────────────
# 모노스페이스 폰트에 한글 글리프가 없으면 주석이 두부(□)로 박힌다.
# 한글 주석은 흔하므로 글자별로 폰트를 갈아 끼운다.


def test_display_runs_splits_hangul_from_ascii() -> None:
    assert display_runs("# 10만 건") == [("# 10", False), ("만", True), (" ", False), ("건", True)]


def test_display_runs_keeps_pure_ascii_as_one_run() -> None:
    assert display_runs("items = set()") == [("items = set()", False)]


def test_display_runs_empty() -> None:
    assert display_runs("") == []


def test_korean_comment_renders_without_tofu() -> None:
    """한글 주석이 있어도 렌더가 성공하고, 없는 경우와 다른 그림이 나와야 한다."""
    with_kr = render_code_square("# 10만 건에서 검색\nx = 1", size=480)
    ascii_only = render_code_square("# search 100k rows\nx = 1", size=480)
    assert with_kr != ascii_only


# ── 초점 줄 (focus_lines) ─────────────────────────────────────────
# 컷이 바뀌어도 코드가 같으면 화면이 정지한다. 설명이 향하는 줄만 밝게 두고 나머지를
# 어둡게 하면, 같은 코드로도 컷마다 화면이 바뀌고 시선이 유도된다.

_SNIPPET = "items = load_ids()\n\nif target in items:\n    handle(target)"


def test_focus_changes_output() -> None:
    plain = render_code_square(_SNIPPET, size=480)
    focused = render_code_square(_SNIPPET, size=480, focus_lines=(3,))
    assert plain != focused


def test_different_focus_different_output() -> None:
    """같은 코드라도 초점이 다르면 다른 그림 — 컷 전환의 시각 변화가 여기서 나온다."""
    a = render_code_square(_SNIPPET, size=480, focus_lines=(1,))
    b = render_code_square(_SNIPPET, size=480, focus_lines=(3,))
    assert a != b


def test_focus_is_deterministic() -> None:
    a = render_code_square(_SNIPPET, size=480, focus_lines=(3, 4))
    b = render_code_square(_SNIPPET, size=480, focus_lines=(3, 4))
    assert hashlib.sha256(a).digest() == hashlib.sha256(b).digest()


def test_focus_order_does_not_matter() -> None:
    a = render_code_square(_SNIPPET, size=480, focus_lines=(4, 3))
    b = render_code_square(_SNIPPET, size=480, focus_lines=(3, 4))
    assert a == b


def test_empty_focus_same_as_none() -> None:
    """빈 초점은 '전부 밝게'와 같아야 한다 — 초점 없음을 특수 케이스로 두지 않는다."""
    assert render_code_square(_SNIPPET, size=480, focus_lines=()) == render_code_square(
        _SNIPPET, size=480
    )


def test_focus_line_out_of_range_rejected() -> None:
    with pytest.raises(CodeImageError, match="초점"):
        render_code_square(_SNIPPET, size=480, focus_lines=(99,))
