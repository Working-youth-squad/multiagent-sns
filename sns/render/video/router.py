"""method → 렌더러 라우팅 (Capability Gate).

**"이 환경이 할 수 있는가"의 단일 출처다.** 라우터에 주입되지 않은 method는 현재 실행
환경에서 선택할 수 없다 — "존재하지 않는다"가 아니다. 같은 spec이 다른 배선의 환경에서는
렌더될 수 있으므로 이 판정은 **환경 범위**다.

이것과 **Cost Gate**([sns.render.video.gen.budget], "이만큼 써도 되는가")를 뭉치면
라우터 등록이 무제한 지출 승인처럼 읽히고, FR-P6의 예산 강제가 설계에서 사라진다.

비싼 method를 배선에 안 적으면 못 켜진다는 것이 이 자리의 안전장치다 —
[sns.render.images.resolve]의 "기본값을 `generate_image`로 두면 결제가 켜진 계정에서
사이클이 조용히 돈을 쓴다"와 같은 규율이다.
"""

from collections.abc import Mapping

from sns.tools.contracts import MediaAsset, MediaKind, RenderMedia, VideoMethod


class VideoRenderRouter:
    """`RenderMedia` 계약을 만족하면서 method로 갈라 보낸다.

    `run_cycle`은 얇은 `RenderMedia`만 알면 되므로 라우터를 그 자리에 그대로 꽂는다.
    `supported_methods`는 Content 에이전트가 고를 후보를 정하는 값이라, 진입점이
    **명시로** `run_cycle`에 넘긴다 — `isinstance`로 라우터를 들춰보면 얇은 계약이 깨진다.
    """

    def __init__(self, renderers: Mapping[VideoMethod, RenderMedia]) -> None:
        self._renderers = dict(renderers)

    @property
    def supported_methods(self) -> tuple[VideoMethod, ...]:
        return tuple(self._renderers)

    def __call__(self, media_spec: Mapping[str, object], kind: MediaKind) -> MediaAsset:
        method = media_spec.get("method", "template")
        # 문자열 키로 찾는다 — media_spec은 jsonb에서 온 `object`라 VideoMethod로 좁혀져
        # 있지 않다. 여기서 캐스트로 우겨넣으면 모르는 문자열이 그대로 통과한다.
        by_name = {str(name): renderer for name, renderer in self._renderers.items()}
        renderer = by_name.get(str(method))
        if renderer is None:
            raise ValueError(
                f"이 환경이 배선하지 않은 제작 방식: {method!r} (가능: {sorted(by_name)})"
            )
        return renderer(media_spec, kind)
