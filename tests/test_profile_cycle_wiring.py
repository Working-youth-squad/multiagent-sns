"""run_profile_cycle의 순수 배선 — DB·LLM 없이 도는 부분만 본다.

스크립트라 통째로는 테스트할 수 없지만, **매핑을 틀리면 되돌릴 수 없는 사고**가 난다
(manual 채널이 자동 발행되는 것처럼). 그 매핑만 떼어 확인한다.
"""

from pathlib import Path

import pytest

from scripts.run_profile_cycle import (
    DirMediaStore,
    build_parser,
    channel_mode_of,
    content_format_for,
    platform_of,
)


def test_manual_mode_is_not_promoted_to_auto() -> None:
    """예전 코드는 manual을 auto로 바꿔 자동 발행 경로에 넣었다."""
    assert channel_mode_of("manual") == "manual"


def test_known_modes_pass_through() -> None:
    assert channel_mode_of("hybrid") == "hybrid"
    assert channel_mode_of("auto") == "auto"


def test_unknown_mode_is_refused() -> None:
    """조용한 폴백이 있으면 오타 하나가 발행 모드를 바꾼다."""
    with pytest.raises(SystemExit):
        channel_mode_of("whatever")


def test_card_format_ignores_platform() -> None:
    assert content_format_for("instagram", "card") == "feed_image"
    assert content_format_for("youtube", "card") == "feed_image"


def test_video_format_follows_platform() -> None:
    assert content_format_for("instagram", "video") == "reels"
    assert content_format_for("youtube", "video") == "shorts"


def test_known_platforms_pass_through() -> None:
    assert platform_of("instagram") == "instagram"
    assert platform_of("youtube") == "youtube"


def test_unknown_platform_is_refused() -> None:
    """조용히 shorts로 떨구면 플랫폼이 늘 때 틀린 포맷이 나간다."""
    with pytest.raises(SystemExit):
        platform_of("tiktok")


def test_parser_defaults_to_card() -> None:
    args = build_parser().parse_args(["my-channel"])
    assert args.format == "card"
    assert args.font is None
    assert args.ffmpeg == "ffmpeg"


def test_parser_accepts_video_and_font() -> None:
    args = build_parser().parse_args(["c", "--format", "video", "--font", "/f.ttf"])
    assert args.format == "video"
    assert args.font == "/f.ttf"


def test_media_store_round_trip(tmp_path: Path) -> None:
    """put이 낸 URL을 get이 되읽어야 한다.

    영상 품질 게이트가 산출 mp4를 되읽는데(`make_video_gate`), 이 store는 file:// URI를
    낸다. 예전엔 get이 NotImplementedError였고 게이트는 URL을 파일 경로로 간주해
    `file:\\C:\\...`로 OSError가 났다 — 실 관통에서 터진 자리다.
    """
    store = DirMediaStore(tmp_path)
    url = store.put(b"\x00\x01mp4", checksum="a" * 64, kind="video", ext="mp4")
    assert store.get(url) == b"\x00\x01mp4"
