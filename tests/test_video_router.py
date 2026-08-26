"""Capability Gate — 이 실행 환경이 할 수 있는 method만 통한다.

"할 수 있는가"는 **배선**이 답한다. Domain/Style은 관여하지 않는다. 비싼 method는 배선에
안 적으면 못 켜진다 — resolve.py의 "기본값을 generate_image로 두면 결제가 켜진 계정에서
사이클이 조용히 돈을 쓴다"와 같은 규율이다.

이건 Cost Gate("이만큼 써도 되는가", [sns.render.video.gen.budget])와 다른 질문이다.
둘을 뭉치면 라우터 등록이 무제한 지출 승인처럼 읽힌다.
"""

from collections.abc import Mapping

import pytest

from sns.render.video.router import VideoRenderRouter
from sns.tools.contracts import MediaAsset, MediaKind


class _Fake:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __call__(self, media_spec: Mapping[str, object], kind: MediaKind) -> MediaAsset:
        return MediaAsset(kind=kind, storage_url=f"mem://{self.tag}", checksum="c" * 64)


def test_routes_by_method() -> None:
    router = VideoRenderRouter({"template": _Fake("t"), "generated_scene": _Fake("g")})
    asset = router({"method": "generated_scene", "slides": []}, "video")
    assert asset.storage_url == "mem://g"


def test_absent_method_defaults_to_template() -> None:
    """method가 없는 기존 media_spec은 템플릿으로 간다."""
    router = VideoRenderRouter({"template": _Fake("t")})
    assert router({"slides": []}, "video").storage_url == "mem://t"


def test_unwired_method_is_refused() -> None:
    """이 환경이 못 하는 method는 렌더 전에 끊는다."""
    router = VideoRenderRouter({"template": _Fake("t")})
    with pytest.raises(ValueError, match="generated_scene"):
        router({"method": "generated_scene", "slides": []}, "video")


def test_unknown_method_string_is_refused() -> None:
    router = VideoRenderRouter({"template": _Fake("t")})
    with pytest.raises(ValueError, match="generated_clip_v2"):
        router({"method": "generated_clip_v2", "slides": []}, "video")


def test_supported_methods_is_the_registry() -> None:
    """이 값이 Content 에이전트의 후보 목록이 된다 — 라우터가 단일 출처다."""
    router = VideoRenderRouter({"template": _Fake("t"), "generated_scene": _Fake("g")})
    assert set(router.supported_methods) == {"template", "generated_scene"}


def test_same_spec_renders_elsewhere_with_other_wiring() -> None:
    """판정은 **환경 범위**다 — "존재하지 않는다"가 아니라 "여기선 못 한다"."""
    spec: Mapping[str, object] = {"method": "generated_scene", "slides": []}
    with pytest.raises(ValueError):
        VideoRenderRouter({"template": _Fake("t")})(spec, "video")
    assert VideoRenderRouter({"generated_scene": _Fake("g")})(spec, "video").storage_url
