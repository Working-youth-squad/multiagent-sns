"""슬라이드별 길이 → ASS 자막 문서.

자막은 safe area(1080×1920 기준 중앙 900×1400 박스, FR-A2) 안에 하단 정렬로
들어간다. 다른 해상도는 같은 비율로 마진을 환산한다.
"""

from collections.abc import Sequence

# 1080×1920 기준 safe area 900×1400 → 비율로 환산해 다른 해상도에도 적용.
_SAFE_X_RATIO = (1080 - 900) / 2 / 1080
_SAFE_V_RATIO = (1920 - 1400) / 2 / 1920
_FONT_SIZE_RATIO = 64 / 1920


def _ass_time(seconds: float) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(
    texts: Sequence[str],
    durations_s: Sequence[float],
    *,
    width: int,
    height: int,
    font: str = "Malgun Gothic",
) -> str:
    """텍스트·길이 쌍 → ASS 문서. 타임코드는 길이의 누적합."""
    if len(texts) != len(durations_s):
        raise ValueError(f"texts({len(texts)})와 durations({len(durations_s)}) 길이 불일치")
    margin_x = round(width * _SAFE_X_RATIO)
    margin_v = round(height * _SAFE_V_RATIO)
    font_size = round(height * _FONT_SIZE_RATIO)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
        f"Style: Default,{font},{font_size},&H00FFFFFF,&H00000000,&H80000000,"
        f"-1,3,0,2,{margin_x},{margin_x},{margin_v}\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    cursor = 0.0
    for text, duration in zip(texts, durations_s, strict=True):
        start, cursor = cursor, cursor + duration
        escaped = text.replace("\n", r"\N")
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(cursor)},Default,,0,0,0,,{escaped}"
        )
    return header + "\n".join(lines) + "\n"
