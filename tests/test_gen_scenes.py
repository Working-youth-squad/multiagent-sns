"""장면 해소 — 생성은 렌더 밖에서 한 번, 실패는 종류를 갖고 기록된다.

`resolve.py`가 `image_query`에 하는 일과 같은 규율이다: 생성 시점에 저장소로 못박아
렌더가 네트워크 없이 결정론으로 돌게 한다(FR-M1).

**실패에 종류가 필요한 이유**: "재실행이 곧 재시도"만으로는 `safety` 실패에서 매 실행마다
유료 호출을 반복한다 — 같은 프롬프트는 같은 안전 판정을 받는데도.
"""

import pytest

from sns.render.images.generate import ImageGenerationError
from sns.render.storage import InMemoryMediaStore
from sns.render.video.gen.budget import BudgetExceeded, ImageBudget
from sns.render.video.gen.scenes import RETRYABLE, classify_failure, resolve_scenes

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_RULES = ("flat vector illustration scene", "no text")


def _spec(*slides: dict[str, object]) -> dict[str, object]:
    return {"topic": "주제", "method": "generated_scene", "slides": list(slides)}


def _cut(**extra: object) -> dict[str, object]:
    return {"subtitle": "부제", "narration": "한 줄.", "scene_prompt": "a warm kitchen", **extra}


def _ok(subject: str, **kw: object) -> bytes:
    return _PNG


def test_generates_and_pins_the_ref() -> None:
    store = InMemoryMediaStore()
    out = resolve_scenes(
        _spec(_cut()), store=store, scene_rules=_RULES, budget=ImageBudget(), generate=_ok
    )
    slides = out.media_spec["slides"]
    ref = slides[0]["scene_ref"]  # type: ignore[index]
    assert ref and store.get(ref) == _PNG


def test_input_spec_is_not_mutated() -> None:
    """해소는 새 spec을 돌려준다 — 원본이 바뀌면 재실행이 달라진다."""
    spec = _spec(_cut())
    resolve_scenes(
        spec, store=InMemoryMediaStore(), scene_rules=_RULES, budget=ImageBudget(), generate=_ok
    )
    assert "scene_ref" not in spec["slides"][0]  # type: ignore[index]


def test_already_resolved_cut_is_skipped() -> None:
    """재실행 멱등 — scene_ref가 있으면 유료 호출을 하지 않는다."""
    calls = 0

    def counting(subject: str, **kw: object) -> bytes:
        nonlocal calls
        calls += 1
        return _PNG

    resolve_scenes(
        _spec(_cut(scene_ref="mem://image/done.png")),
        store=InMemoryMediaStore(),
        scene_rules=_RULES,
        budget=ImageBudget(),
        generate=counting,
    )
    assert calls == 0


def test_non_retryable_failure_is_skipped_next_time() -> None:
    """safety는 같은 프롬프트에 같은 판정이다 — 다시 부르면 돈만 쓴다."""
    calls = 0

    def counting(subject: str, **kw: object) -> bytes:
        nonlocal calls
        calls += 1
        return _PNG

    resolve_scenes(
        _spec(_cut(scene_failure={"kind": "safety"})),
        store=InMemoryMediaStore(),
        scene_rules=_RULES,
        budget=ImageBudget(),
        generate=counting,
    )
    assert calls == 0


def test_retryable_failure_is_retried() -> None:
    """quota는 시간이 지나면 풀린다 — 다음 실행에서 다시 시도한다."""
    out = resolve_scenes(
        _spec(_cut(scene_failure={"kind": "quota"})),
        store=InMemoryMediaStore(),
        scene_rules=_RULES,
        budget=ImageBudget(),
        generate=_ok,
    )
    slide = out.media_spec["slides"][0]  # type: ignore[index]
    assert slide["scene_ref"]
    assert "scene_failure" not in slide, "성공했는데 옛 실패 기록이 남았다"


def test_failure_is_recorded_with_a_kind() -> None:
    def boom(subject: str, **kw: object) -> bytes:
        raise ImageGenerationError("[google] 이미지 생성 할당량 초과(429) — 결제를 켜세요")

    out = resolve_scenes(
        _spec(_cut()),
        store=InMemoryMediaStore(),
        scene_rules=_RULES,
        budget=ImageBudget(),
        generate=boom,
    )
    slide = out.media_spec["slides"][0]  # type: ignore[index]
    assert slide["scene_failure"]["kind"] == "quota"
    assert out.notes, "사람이 읽을 사유도 남아야 한다"


def test_budget_exceeded_is_not_swallowed() -> None:
    """예산 초과는 폴백이 아니라 사고 — 컷 기록으로 삼키지 않고 던진다."""
    with pytest.raises(BudgetExceeded):
        resolve_scenes(
            _spec(_cut(), _cut()),
            store=InMemoryMediaStore(),
            scene_rules=_RULES,
            budget=ImageBudget(limit=1),
            generate=_ok,
        )


def test_blocked_prompt_never_reaches_the_api() -> None:
    """금지 소재는 요청 전에 끊는다(FR-Q7, 유료 호출 방어)."""

    def never(subject: str, **kw: object) -> bytes:
        raise AssertionError("호출되면 안 된다")

    out = resolve_scenes(
        _spec(_cut(scene_prompt="a nude portrait")),
        store=InMemoryMediaStore(),
        scene_rules=_RULES,
        budget=ImageBudget(),
        generate=never,
    )
    slide = out.media_spec["slides"][0]  # type: ignore[index]
    assert slide["scene_failure"]["kind"] == "safety"


def test_blocked_prompt_does_not_spend_budget() -> None:
    """돈을 안 썼으면 예산도 안 줄어야 한다."""
    budget = ImageBudget()
    resolve_scenes(
        _spec(_cut(scene_prompt="a nude portrait")),
        store=InMemoryMediaStore(),
        scene_rules=_RULES,
        budget=budget,
        generate=_ok,
    )
    assert budget.spent == 0


def test_template_spec_is_untouched() -> None:
    """다른 method의 spec은 건드리지 않는다."""
    spec: dict[str, object] = {
        "topic": "주제",
        "method": "template",
        "slides": [{"subtitle": "부제", "narration": "한 줄.", "code": "print(1)"}],
    }
    out = resolve_scenes(
        spec, store=InMemoryMediaStore(), scene_rules=_RULES, budget=ImageBudget(), generate=_ok
    )
    assert out.media_spec == spec


def test_retryable_set_is_explicit() -> None:
    assert RETRYABLE == frozenset({"quota", "network"})


def test_classification_by_cause() -> None:
    assert classify_failure(ImageGenerationError("할당량 초과(429)")) == "quota"
    assert classify_failure(ImageGenerationError("safety filter blocked")) == "safety"
    assert classify_failure(OSError("connection reset")) == "network"
    assert classify_failure(ImageGenerationError("응답 형식이 예상과 다름")) == "provider"
