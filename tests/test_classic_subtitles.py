"""ASS 자막 — 타임코드 누적과 safe-area 마진."""

import pytest

from sns.render.video.classic.subtitles import build_ass


def test_cumulative_timecodes() -> None:
    ass = build_ass(["첫 장", "둘째 장"], [2.5, 3.0], width=1080, height=1920)
    assert "Dialogue: 0,0:00:00.00,0:00:02.50,Default,,0,0,0,,첫 장" in ass
    assert "Dialogue: 0,0:00:02.50,0:00:05.50,Default,,0,0,0,,둘째 장" in ass


def test_safe_area_margins_1080x1920() -> None:
    # safe area 중앙 900×1400 → 좌우 90, 상하 260 (FR-A2)
    ass = build_ass(["x"], [1.0], width=1080, height=1920)
    assert ",90,90,260" in ass
    assert "PlayResX: 1080" in ass


def test_newline_escaped_and_length_mismatch() -> None:
    ass = build_ass(["줄1\n줄2"], [1.0], width=1080, height=1920)
    assert r"줄1\N줄2" in ass
    with pytest.raises(ValueError):
        build_ass(["a", "b"], [1.0], width=1080, height=1920)
