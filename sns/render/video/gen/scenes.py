"""장면 해소 — `scene_prompt` → `scene_ref`. 생성은 렌더 밖에서 한 번.

[sns.render.images.resolve]가 `image_query`에 하는 일과 같은 규율이다:

    scene_prompt ──(생성 시점 1회)──▶ 이미지 생성 ──▶ 저장소 ──▶ scene_ref
                                                                  │
              렌더러가 읽는 것은 scene_ref 뿐 ◀────────────────────┘

렌더가 네트워크 없이 결정론으로 돈다(FR-M1). **정직한 한계**: 보장하는 것은 "확정된
spec → 같은 영상"이고 "같은 프롬프트 → 같은 영상"은 아니다.

**실패는 종류를 갖는다.** "재실행이 곧 재시도"만으로는 `safety` 실패에서 매 실행마다
유료 호출을 반복한다 — 같은 프롬프트는 같은 안전 판정을 받는데도. 그래서 실패를 컷에
기록하고, 재시도 불가면 다음 실행이 건너뛴다. 별도 retry 루프는 두지 않는다 — 사이클
재실행이 곧 재시도다.
"""

import copy
import hashlib
import urllib.error
from collections.abc import Callable, Mapping, Sequence

from sns.render.images.gate import screen_query
from sns.render.images.generate import ImageGenerationError, build_prompt, generate_image
from sns.render.images.resolve import ImageResolution
from sns.render.storage import MediaStore
from sns.render.video.gen.budget import ImageBudget
from sns.render.video.spec import FAILURE_FIELD
from sns.tools.contracts import GenerationFailureKind

GenerateImage = Callable[..., bytes]

# 재시도 가능한 실패. quota는 시간이 지나면 풀리고 network는 다음 실행에서 성공할 수 있다.
# safety·provider는 같은 입력에 같은 결과라 다시 부르면 돈만 쓴다.
RETRYABLE: frozenset[GenerationFailureKind] = frozenset({"quota", "network"})

# 발행 쪽 `ErrorClass`와 이름을 통일하지 않은 이유는 실패의 성격이 달라서다. 다만
# **재시도 가능/불가라는 축은 같다** — 그 축을 두 곳에서 다르게 부르지 않도록 병기한다.
_QUOTA_MARKS = ("429", "quota", "할당량", "rate limit", "resource_exhausted")
_SAFETY_MARKS = ("safety", "blocked", "금지", "policy", "차단")


def classify_failure(exc: BaseException) -> GenerationFailureKind:
    """예외 → 실패 종류. 재시도 여부를 가르는 유일한 판정이다."""
    if isinstance(exc, urllib.error.URLError | TimeoutError | OSError) and not isinstance(
        exc, ImageGenerationError
    ):
        return "network"
    text = str(exc).lower()
    if any(mark in text for mark in _QUOTA_MARKS):
        return "quota"
    if any(mark in text for mark in _SAFETY_MARKS):
        return "safety"
    return "provider"


def _should_skip(slide: Mapping[str, object]) -> bool:
    """이미 해소됐거나 다시 시도해도 소용없는 컷인가."""
    if slide.get("scene_ref"):
        return True
    failure = slide.get(FAILURE_FIELD)
    if isinstance(failure, Mapping):
        kind = failure.get("kind")
        return isinstance(kind, str) and kind not in RETRYABLE
    return False


def _record(slide: dict[str, object], kind: GenerationFailureKind, detail: str) -> None:
    slide.pop("scene_ref", None)
    slide[FAILURE_FIELD] = {"kind": kind, "detail": detail[:200]}


def resolve_scenes(
    media_spec: Mapping[str, object],
    *,
    store: MediaStore,
    scene_rules: Sequence[str],
    budget: ImageBudget,
    generate: GenerateImage = generate_image,
) -> ImageResolution:
    """`scene_prompt`를 전부 해소한 **새 spec**을 돌려준다(입력 불변).

    `BudgetExceeded`는 잡지 않고 올려보낸다 — 예산 초과는 폴백이 아니라 사고다.
    """
    if media_spec.get("method") != "generated_scene":
        return ImageResolution(media_spec=dict(media_spec))

    slides = media_spec.get("slides")
    if not isinstance(slides, list):
        raise ValueError(f"'slides'는 리스트여야 함: {slides!r}")

    resolved: list[object] = copy.deepcopy(slides)
    notes: list[str] = []
    for index, slide in enumerate(resolved):
        if not isinstance(slide, dict):
            raise ValueError(f"'slides[{index}]'는 객체여야 함: {slide!r}")
        if _should_skip(slide):
            continue
        prompt = str(slide.get("scene_prompt", "")).strip()
        if not prompt:
            continue  # 파서가 이미 막지만, 해소가 그 가정에 기대지 않는다

        # 금지 소재는 **요청 전에** 끊는다(FR-Q7). 예산도 쓰지 않는다 — 돈을 안 썼으니까.
        verdict = screen_query(prompt)
        if not verdict.allowed:
            _record(slide, "safety", verdict.reason)
            notes.append(f"컷 {index + 1}: 장면 생성 차단 — {verdict.reason}")
            continue

        budget.spend()  # BudgetExceeded는 여기서 위로 던져진다
        try:
            data = generate(build_prompt(prompt, scene_rules))
        except Exception as exc:  # noqa: BLE001 — 종류를 갈라 기록하는 게 이 자리의 일이다
            kind = classify_failure(exc)
            _record(slide, kind, str(exc))
            notes.append(f"컷 {index + 1}: 장면 생성 실패({kind}) — {exc}")
            continue

        checksum = hashlib.sha256(data).hexdigest()
        slide["scene_ref"] = store.put(data, checksum=checksum, kind="image", ext="png")
        slide.pop(FAILURE_FIELD, None)  # 성공했으면 옛 실패 기록을 지운다

    return ImageResolution(media_spec={**media_spec, "slides": resolved}, notes=tuple(notes))
