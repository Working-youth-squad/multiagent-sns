"""포맷별 렌더 배선 — **진입점들이 공유하는 정본**.

`run_cycle`이 요구하는 세 협력자(`render_media`·`assess_quality`·`resolve_media_spec`)와
Content 에이전트가 고를 `supported_methods`를 한 번에 조립한다. 갈림은 하나다:

    card  → 카드 렌더러 + 카드 게이트 (해소 없음)
    video → 영상 라우터 + 영상 게이트 + 이미지·장면 해소

**`ContentFormat`이 아니라 `FormatChoice`를 받는다.** 릴스와 쇼츠는 배선이 같다 —
다른 것은 규격뿐이고 그건 spec이 정한다. `ContentFormat`을 받으면 대상마다 값이 다른
사이클(인스타 릴스 + 유튜브 쇼츠를 한 번에)에서 "어느 대상의 포맷을 넘길 것인가"라는
답 없는 질문이 생기고, 호출부가 `targets[0]`을 집는 모양이 된다.

**여기 있는 이유.** 이 배선은 프로필 CLI(`scripts/run_profile_cycle.py`)와 키워드
챗봇 웹(`scripts/run_chat_web.py`) 둘이 똑같이 필요하다. 진입점마다 적으면 한쪽만
고쳐진다 — 실제로 그 사고가 있었다. 옛 배선 블록이 지워지지 않은 채 새 블록 뒤에
남아 `--format video --style 3col`이 **영상 라우터를 카드 렌더러로 덮어썼다**(영상을
요청했는데 이미지가 나온다). 배선이 한 벌이면 그 모양의 사고가 생길 자리가 없다.

**Capability Gate는 여기서 닫힌다.** 라우터 dict에 적힌 method만 고를 수 있고, 비싼
method는 호출자가 `methods=`로 명시해야 들어온다([sns.render.video.router]). 기본값을
넓히면 결제가 켜진 계정에서 사이클이 조용히 돈을 쓴다.

**Cost Gate는 여기가 아니다**([sns.render.video.gen.budget]). 예산은 사이클 하나에
하나라, `build_render_wiring` 호출 하나가 곧 예산 하나다 — 돌려받은 배선을 두 사이클에
재사용하면 이전 소비가 이어져 계산이 어긋난다.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sns.onboarding.character import make_scene_generate, scene_rules_for
from sns.quality.gate import QualityReport, check_card
from sns.render.card.media import CardRenderMedia
from sns.render.card.spec import parse_card_spec
from sns.render.images.generate import generate_image
from sns.render.images.resolve import GenerateImage, ImageResolution, resolve_images
from sns.render.storage import MediaStore
from sns.render.video.gen.budget import ImageBudget
from sns.render.video.gen.media import SceneRenderMedia
from sns.render.video.gen.scenes import resolve_scenes
from sns.render.video.media import VideoRenderMedia
from sns.render.video.quality import make_video_gate
from sns.render.video.router import VideoRenderRouter
from sns.render.video.tts import Synthesize, synthesize_google
from sns.runner.cycle import AssessQuality, ResolveMediaSpec
from sns.runner.formats import FormatChoice
from sns.tools.contracts import ContentFormat, MediaAsset, RenderMedia, VideoMethod

VIDEO_STYLES = ("3col", "motion", "clip")
"""화면 문법 — `method`와 직교한다([sns.render.video.spec.VIDEO_STYLES]).

CLI/대화가 쓰는 표기다. spec의 `style`은 3단 레이아웃을 빈 문자열로 적으므로
`spec_style`이 옮긴다 — 사람에게 `""`를 고르라고 할 수는 없다.
"""


def spec_style(style: str) -> str:
    """사람이 고른 화면 문법 → `media_spec`의 `style` 값."""
    return "" if style == "3col" else style


@dataclass(frozen=True)
class RenderWiring:
    """`run_cycle`에 그대로 넘길 협력자 묶음.

    **한 사이클에 하나다.** `resolve`가 예산(`ImageBudget`)을 닫아 물고 있어서, 두
    사이클이 같은 배선을 쓰면 첫 사이클의 소비가 둘째 사이클로 이어진다.
    """

    render_media: RenderMedia
    assess_quality: AssessQuality | None
    resolve_media_spec: ResolveMediaSpec | None
    supported_methods: tuple[VideoMethod, ...]


def _card_wiring(store: MediaStore, *, font: str | None) -> RenderWiring:
    renderer = CardRenderMedia(store, font_path=font)

    def assess_card(
        *,
        media_spec: Mapping[str, object],
        media: MediaAsset,
        content_format: ContentFormat,
    ) -> QualityReport:
        return check_card(parse_card_spec(media_spec), renderer.render(media_spec))

    # 카드는 method라는 축이 없다. 그래도 template을 돌려주는 이유는 Content 에이전트가
    # 이 목록을 프롬프트에 싣기 때문이다 — 빈 튜플을 주면 "고를 게 없다"가 된다.
    return RenderWiring(renderer, assess_card, None, ("template",))


def _read_bytes(url: str | None) -> bytes | None:
    """캐릭터 앵커 이미지를 읽는다. `file://` URI와 평문 경로를 모두 받는다.

    저장소 구현이 진입점마다 달라 둘 다 온다([sns.render.storage] 벤더 교체 seam).
    """
    if not url:
        return None
    from urllib.parse import urlparse
    from urllib.request import url2pathname

    parsed = urlparse(url)
    path = Path(url2pathname(parsed.path)) if parsed.scheme == "file" else Path(url)
    return path.read_bytes()


def build_render_wiring(
    *,
    kind: FormatChoice,
    store: MediaStore,
    topic_major: str,
    font: str | None = None,
    style: str = "motion",
    methods: Sequence[VideoMethod] = ("template",),
    character_image_url: str | None = None,
    character_style: str = "",
    ffmpeg: str = "ffmpeg",
    ffprobe: str | None = None,
    synthesize: Synthesize = synthesize_google,
    generate: GenerateImage = generate_image,
) -> RenderWiring:
    """카드냐 영상이냐로 갈라 `run_cycle`의 협력자를 조립한다.

    `methods`는 **라우터에 등록할 목록**이다(Capability Gate). `generated_scene`은 컷마다
    유료 이미지를 생성하므로 호출자가 명시해야 들어온다 — 기본값에 두지 않는다.

    `character_image_url`이 있으면 정사각 생성의 **레퍼런스**로 쓴다. 없으면 일반 생성
    이미지로 간다 — 캐릭터 미선택이 글자만 남은 영상이 되지 않게(스톡 폴백은 그대로).
    """
    if kind == "card":
        return _card_wiring(store, font=font)

    if not methods:
        raise ValueError("영상 배선에 methods가 비어 있다 — 라우터가 아무것도 못 고른다")

    renderers: dict[VideoMethod, RenderMedia] = {}
    for method in methods:
        if method == "template":
            renderers["template"] = VideoRenderMedia(
                store,
                synthesize=synthesize,
                topic_major=topic_major,
                font_path=font,
                ffmpeg=ffmpeg,
            )
        elif method == "generated_scene":
            renderers["generated_scene"] = SceneRenderMedia(
                store,
                synthesize=synthesize,
                topic_major=topic_major,
                font_path=font,
                ffmpeg=ffmpeg,
            )
        else:
            # 조용히 빼면 대화·CLI가 고를 수 있다고 안내한 방식이 라우터에서 사라진다 —
            # 사용자는 확정한 뒤에야 "배선하지 않은 제작 방식"을 만난다.
            raise ValueError(f"이 배선이 모르는 제작 방식: {method!r}")

    router = VideoRenderRouter(renderers)
    probe = ffprobe or (str(Path(ffmpeg).parent / "ffprobe") if ffmpeg != "ffmpeg" else "ffprobe")
    assess = make_video_gate(store.get, ffprobe=probe, ffmpeg=ffmpeg)

    anchor = _read_bytes(character_image_url)
    scene_generate = make_scene_generate(anchor) if anchor is not None else generate
    # 예산은 사이클 하나에 하나 — 재사용하면 이전 소비가 이어져 계산이 어긋난다.
    budget = ImageBudget()
    rules = scene_rules_for(character_style)
    pinned = spec_style(style)

    def extras() -> dict[str, object]:
        """spec에 못박는 값 — 배선으로만 넘기면 같은 media_spec이 채널마다 다른 mp4를
        낳아 FR-M1이 깨지고, 승인 웹 재렌더가 같은 꼴로 못 돈다."""
        extra: dict[str, object] = {}
        if pinned:
            extra["style"] = pinned
        if character_image_url:
            extra["character_ref"] = character_image_url
        return extra

    def resolve(spec: Mapping[str, object]) -> ImageResolution:
        res = resolve_images(spec, store=store, generate=scene_generate)
        # 장면 해소 — generated_scene이 아니면 resolve_scenes가 그대로 통과시킨다.
        res = resolve_scenes(res.media_spec, store=store, scene_rules=rules, budget=budget)
        return ImageResolution({**res.media_spec, **extras()}, res.notes)

    return RenderWiring(router, assess, resolve, router.supported_methods)


def extras_only_resolve(
    *, style: str, character_image_url: str | None
) -> Callable[[Mapping[str, object]], ImageResolution]:
    """해소 없이 spec 고정값만 붙이는 `ResolveMediaSpec`.

    대본 단계(`--script-only`)가 쓴다 — 렌더·이미지 해소(과금)를 하지 않으면서도
    `style`·`character_ref`는 그때 못박아야 나중 `--render-item`이 같은 꼴로 렌더한다.
    """
    pinned = spec_style(style)

    def resolve(spec: Mapping[str, object]) -> ImageResolution:
        extra: dict[str, object] = {}
        if pinned:
            extra["style"] = pinned
        if character_image_url:
            extra["character_ref"] = character_image_url
        return ImageResolution({**spec, **extra})

    return resolve
