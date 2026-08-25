# Slide 정사각 어휘 중립화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `Slide`가 `code`·`lang`·`focus_lines`·`image_query`·`image_prompt`·`image_ref` 여섯 개의 개발 도메인 어휘를 들고 다니는 것을, 소스 이름이 붙은 `square` 페이로드 하나로 바꾼다.

**Architecture:** `Slide.square: SquarePayload | None`을 도입한다. `SquarePayload`는 `CodeSquare | ConceptSquare | ImageSquare` 판별 유니온이고, JSON에서는 `{"source": "code", ...}`처럼 `source` 키로 갈린다. 파서는 도메인 팩의 `square_sources`로 허용 여부를 판정하고, 렌더러는 페이로드 타입으로 분기한다. **구형(평면) 스펙은 읽기만 지원**한다 — DB에 이미 저장된 `media_spec`을 재렌더할 수 있어야 하기 때문이다.

**Tech Stack:** Python 3.12, dataclasses, pytest, Pillow, pygments(코드 컷에서만 지연 로드)

**Spec:** [docs/plan/15-구현-이탈기록.md](../../plan/15-구현-이탈기록.md) §1.8 — 도메인 팩 분리의 남은 결합. 이 계획이 그 §1.8이 "남은 것"으로 지목한 데이터 모델 어휘를 처리한다.

## Global Constraints

- 한국어 주석·docstring. 기존 모듈의 서술 밀도를 따른다(무엇이 아니라 **왜**).
- `ruff check .` · `ruff format --check .` · `mypy sns` · `pytest` 네 단계가 모두 통과해야 커밋한다. CI가 그 순서다.
- **기존 테스트를 고치는 것과 깨뜨리는 것은 다르다.** 이 계획은 스키마를 바꾸므로 테스트 픽스처 수정은 정상이다. 다만 *단언*을 약화시켜 통과시키는 것은 금지다.
- 결정론(`FR-M1`): 같은 `media_spec` + 같은 팩 → 같은 checksum. 어느 단계에서도 이 성질을 깨지 않는다.
- `ruff format`은 **마크다운 안의 python 코드펜스도 포맷한다.** 문서에 정렬을 유지하고 싶은 블록은 언어 표기를 붙이지 않는다.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

## 왜 이걸 하는가 (배경)

팀 리뷰에서 나온 지적이다 — *"모듈로 분리했다는데 아직 개발자 의존성이 보인다."*

도메인 팩(#34)은 **값**을 뺐고, 후속 리팩터는 **import 그래프**를 뒤집었다. 남은 건 **어휘**다:

```
@dataclass(frozen=True)
class Slide:
    subtitle: str
    narration: str
    code: str = ""                      ← 정원 도메인의 Slide 도 이걸 들고 다닌다
    lang: str = ""                      ← "pygments 렉서 이름"
    focus_lines: tuple[int, ...] = ()   ← "밝게 둘 코드 줄"
    concept: Concept | None = None
    image_query: str = ""
    image_prompt: str = ""
    image_ref: str = ""
```

파서가 팩에 없는 소스를 거부하긴 한다(#34). 하지만 **모델 자체가 개발 도메인 어휘**라, 코드를 읽는 사람에게는 여전히 "이 시스템은 코드 영상용"으로 보인다. 헬퍼 시그니처에도 번졌다 — `_parse_focus(raw, where, code)`, `_parse_image_text(raw, key, where, code, max_len)`.

## 영향 범위 (측정치)

`focus_lines|image_query|image_prompt|image_ref` 언급 기준:

```
48  tests/test_video_spec.py          8  sns/render/images/resolve.py
21  sns/render/video/spec.py          6  sns/domain/developer.py
17  tests/test_image_resolve.py       5  tests/test_video_render.py
10  tests/test_code_image.py          5  tests/test_domain.py
                                      5  tests/test_cycle_runner.py
                                      4  sns/render/video/renderer.py
```

20개 파일, 약 140곳. **테스트가 절반 이상**이라 실제 위험은 낮지만 손이 많이 간다.

## 결정: 구형 스펙 호환

`media_spec`은 `content_item`에 jsonb로 저장되고 재렌더 입력이 된다(`FR-G3`). 이미 발행된 콘텐츠의 spec은 전부 구형이다.

**읽기는 지원하고 쓰기는 신형만 한다.** 파서가 구형 평면 필드를 만나면 `SquarePayload`로 정규화한다. 에이전트 프롬프트는 신형만 안내하므로 새 spec은 전부 신형이 된다.

제거 조건은 Task 6에 명시한다 — 구형 행이 남아 있는 한 이 경로는 유지한다.

## File Structure

| 파일 | 책임 |
|---|---|
| `sns/render/video/square_spec.py` (신규) | `SquarePayload` 타입 3종 + JSON 파싱/검증. spec.py에서 떼어내는 이유는 spec.py가 이미 272줄이고 정사각 검증만 100줄 가까이 되기 때문 |
| `sns/render/video/spec.py` (수정) | `Slide.square` 필드로 전환, 구형 정규화, 팩 소스 판정 위임 |
| `sns/render/video/renderer.py` (수정) | `_square`가 페이로드 타입으로 분기 |
| `sns/render/images/resolve.py` (수정) | `image_ref` 쓰기를 `square.ref`로 |
| `sns/domain/developer.py` (수정) | `square_guidance` 문구를 신형 스키마로 |
| `tests/test_square_spec.py` (신규) | 페이로드 파싱·검증 |
| 기존 테스트 6개 (수정) | 픽스처를 신형으로. 구형 호환 테스트는 별도 추가 |

---

### Task 1: SquarePayload 타입과 파서

**Files:**
- Create: `sns/render/video/square_spec.py`
- Test: `tests/test_square_spec.py`

**Interfaces:**
- Consumes: `sns.render.concept_image.Concept`, `parse_concept(raw, *, kinds)`
- Produces: `CodeSquare(text, lang, focus)`, `ConceptSquare(concept)`, `ImageSquare(query, prompt, ref, source_url, credit)`, `SquarePayload`, `SquareSpecError`, `parse_square(raw: Mapping[str, object], where: str, *, sources: tuple[str, ...], kinds: tuple[str, ...]) -> SquarePayload`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_square_spec.py
"""정사각 페이로드 — 소스 이름이 붙은 하나의 필드로 슬라이드 어휘를 중립화한다."""

import pytest

from sns.render.video.square_spec import (
    CodeSquare,
    ConceptSquare,
    ImageSquare,
    SquareSpecError,
    parse_square,
)

ALL = ("code", "concept", "image", "gradient")
KINDS = ("emphasis", "compare")


def test_code_square_carries_text_lang_and_focus() -> None:
    got = parse_square(
        {"source": "code", "text": "print(1)\nprint(2)", "lang": "python", "focus": [2]},
        "'slides[0]'의 ",
        sources=ALL,
        kinds=KINDS,
    )
    assert got == CodeSquare(text="print(1)\nprint(2)", lang="python", focus=(2,))


def test_image_square_carries_query_prompt_ref() -> None:
    got = parse_square(
        {"source": "image", "query": "server rack"},
        "'slides[0]'의 ",
        sources=ALL,
        kinds=KINDS,
    )
    assert got == ImageSquare(query="server rack")


def test_concept_square_delegates_validation() -> None:
    got = parse_square(
        {"source": "concept", "kind": "emphasis", "tag": "태그", "headline": "100억", "sub": "부연"},
        "'slides[0]'의 ",
        sources=ALL,
        kinds=KINDS,
    )
    assert isinstance(got, ConceptSquare)
    assert got.concept.kind == "emphasis"


def test_source_outside_the_pack_is_rejected() -> None:
    """팩이 코드를 안 쓰면 코드 페이로드도 못 들어온다 — 사유에 쓸 수 있는 소스를 적는다."""
    with pytest.raises(SquareSpecError, match="concept"):
        parse_square(
            {"source": "code", "text": "print(1)"},
            "'slides[0]'의 ",
            sources=("concept", "gradient"),
            kinds=KINDS,
        )


def test_unknown_source_is_rejected() -> None:
    with pytest.raises(SquareSpecError, match="source"):
        parse_square({"source": "hologram"}, "'slides[0]'의 ", sources=ALL, kinds=KINDS)


def test_unknown_field_is_rejected() -> None:
    """오타를 조용히 무시하면 에이전트가 같은 실수를 반복한다."""
    with pytest.raises(SquareSpecError, match="langauge"):
        parse_square(
            {"source": "code", "text": "print(1)", "langauge": "python"},
            "'slides[0]'의 ",
            sources=ALL,
            kinds=KINDS,
        )


def test_focus_without_code_text_is_rejected() -> None:
    with pytest.raises(SquareSpecError, match="focus"):
        parse_square(
            {"source": "code", "text": "", "focus": [1]}, "'slides[0]'의 ", sources=ALL, kinds=KINDS
        )
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/test_square_spec.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sns.render.video.square_spec'`

- [ ] **Step 3: 최소 구현을 쓴다**

```python
# sns/render/video/square_spec.py
"""정사각 페이로드 — 가운데 칸에 무엇을 넣을지 **하나의 필드**로 표현한다.

예전에는 슬라이드가 `code`·`lang`·`focus_lines`·`image_query`·`image_prompt`·`image_ref`
여섯 필드를 평평하게 들고 다녔다. 코드가 없는 도메인의 슬라이드도 그 어휘를 지고 다녔고,
헬퍼 시그니처에까지 번졌다(`_parse_focus(raw, where, code)`).

`source`로 갈리는 페이로드 하나로 바꾸면 슬라이드는 "정사각에 뭔가 들어간다"만 알면 된다.
무엇이 들어가는지는 도메인 팩([sns.domain])이 정한다.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from sns.render.concept_image import Concept, ConceptError, parse_concept

MAX_QUERY_LEN = 60
MAX_PROMPT_LEN = 200
# 코드 줄 상한 — [sns.render.code_image]와 같은 값. 여기 복제하는 건 이 모듈이 pygments를
# 물지 않게 하기 위해서다([tests/test_render_layering.py]가 두 값의 일치를 강제한다).
MAX_CODE_LINES = 18


class SquareSpecError(ValueError):
    """정사각 페이로드가 잘못됨 — 렌더 진입 전 차단."""


@dataclass(frozen=True)
class CodeSquare:
    text: str
    lang: str = ""  # 문법 강조 렉서 이름. 비면 추측한다
    focus: tuple[int, ...] = ()  # 밝게 둘 줄(1-기반). 나머지는 눌린다


@dataclass(frozen=True)
class ConceptSquare:
    concept: Concept


@dataclass(frozen=True)
class ImageSquare:
    query: str = ""  # 스톡 검색어(영문)
    prompt: str = ""  # 생성 이미지 구도(영문)
    ref: str = ""  # 해소된 저장소 URL — 렌더러가 읽는 건 이쪽뿐이다
    # 출처 표기 — 렌더 입력이 아니라 감사·캡션용이다([sns.render.images.credit]).
    # 해소 시점에 채워지므로 에이전트는 쓰지 않지만, 재파싱 때 살아남아야 한다.
    source_url: str = ""
    credit: str = ""


SquarePayload = CodeSquare | ConceptSquare | ImageSquare

_FIELDS: dict[str, tuple[str, ...]] = {
    "code": ("text", "lang", "focus"),
    "image": ("query", "prompt", "ref", "source_url", "credit"),
}


def _text(raw: Mapping[str, object], key: str, where: str) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str):
        raise SquareSpecError(f"{where}square.{key}는 문자열이어야 함: {value!r}")
    return value


def _reject_unknown(raw: Mapping[str, object], allowed: tuple[str, ...], where: str) -> None:
    """모르는 키는 거부한다 — 오타를 삼키면 에이전트가 같은 실수를 반복한다."""
    for key in raw:
        if key != "source" and key not in allowed:
            raise SquareSpecError(
                f"{where}square가 모르는 필드: {key!r} (허용: {list(allowed)})"
            )


def _parse_code(raw: Mapping[str, object], where: str) -> CodeSquare:
    _reject_unknown(raw, _FIELDS["code"], where)
    text = _text(raw, "text", where)
    if text.strip() and len(text.rstrip().split("\n")) > MAX_CODE_LINES:
        raise SquareSpecError(
            f"{where}square.text는 {MAX_CODE_LINES}줄 이하여야 함: "
            f"{len(text.rstrip().split(chr(10)))}줄"
        )
    focus_raw = raw.get("focus", ())
    if not isinstance(focus_raw, list | tuple) or not all(
        isinstance(n, int) and not isinstance(n, bool) and n >= 1 for n in focus_raw
    ):
        raise SquareSpecError(f"{where}square.focus는 1 이상 정수 목록이어야 함: {focus_raw!r}")
    focus = tuple(int(n) for n in focus_raw)
    if focus and not text.strip():
        raise SquareSpecError(f"{where}square.focus는 text 없이 쓸 수 없음")
    if focus and max(focus) > len(text.rstrip().split("\n")):
        raise SquareSpecError(f"{where}square.focus가 코드 줄 수를 넘음: {focus}")
    return CodeSquare(text=text, lang=_text(raw, "lang", where), focus=focus)


def _parse_image(raw: Mapping[str, object], where: str) -> ImageSquare:
    _reject_unknown(raw, _FIELDS["image"], where)
    query, prompt = _text(raw, "query", where), _text(raw, "prompt", where)
    for key, value, cap in (("query", query, MAX_QUERY_LEN), ("prompt", prompt, MAX_PROMPT_LEN)):
        if len(value) > cap:
            raise SquareSpecError(f"{where}square.{key}는 {cap}자 이하여야 함: {len(value)}자")
        if value and not value.isascii():
            raise SquareSpecError(f"{where}square.{key}는 영문이어야 함: {value!r}")
    return ImageSquare(
        query=query,
        prompt=prompt,
        ref=_text(raw, "ref", where),
        source_url=_text(raw, "source_url", where),
        credit=_text(raw, "credit", where),
    )


def parse_square(
    raw: Mapping[str, object],
    where: str,
    *,
    sources: tuple[str, ...],
    kinds: tuple[str, ...],
) -> SquarePayload:
    """`square` 매핑 → 검증된 페이로드. 팩이 안 쓰는 소스는 거부한다."""
    source = raw.get("source")
    if not isinstance(source, str) or source not in ("code", "concept", "image"):
        raise SquareSpecError(
            f"{where}square.source는 code·concept·image 중 하나여야 함: {source!r}"
        )
    if source not in sources:
        raise SquareSpecError(
            f"{where}square.source={source!r}는 이 도메인이 쓰지 않음 — "
            f"쓸 수 있는 소스: {list(sources)}"
        )
    if source == "code":
        return _parse_code(raw, where)
    if source == "image":
        return _parse_image(raw, where)
    try:
        return ConceptSquare(concept=parse_concept({k: v for k, v in raw.items() if k != "source"}, kinds=kinds))
    except ConceptError as exc:
        raise SquareSpecError(f"{where}square(concept)가 잘못됨 — {exc}") from exc
```

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/test_square_spec.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: 네 단계를 돌린다**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy sns && uv run pytest -q`
Expected: 전부 통과. 이 시점에는 아직 아무도 `square_spec`을 쓰지 않으므로 기존 테스트 수는 그대로여야 한다.

- [ ] **Step 6: 커밋**

```bash
git add sns/render/video/square_spec.py tests/test_square_spec.py
git commit -m "feat(render): 정사각 페이로드 타입 — 슬라이드 어휘 중립화의 토대

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Slide.square 전환 + 구형 스펙 정규화

**Files:**
- Modify: `sns/render/video/spec.py`
- Test: `tests/test_video_spec.py`

**Interfaces:**
- Consumes: Task 1의 `parse_square`, `SquarePayload`, `SquareSpecError`
- Produces: `Slide(subtitle: str, narration: str, square: SquarePayload | None = None)`. `Slide.code`·`lang`·`focus_lines`·`concept`·`image_query`·`image_prompt`·`image_ref` **삭제**. `normalize_legacy_slide(raw: Mapping[str, object]) -> dict[str, object]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_video_spec.py 에 추가
def test_slide_carries_a_single_square_payload() -> None:
    from sns.render.video.square_spec import CodeSquare

    spec = parse_video_spec(
        {
            **MINIMAL,
            "slides": [
                {
                    "subtitle": "왜 느린가",
                    "narration": "in 연산자는 처음부터 끝까지 훑습니다.",
                    "square": {"source": "code", "text": "x in xs", "lang": "python"},
                }
            ],
        }
    )
    assert spec.slides[0].square == CodeSquare(text="x in xs", lang="python")
    assert not hasattr(spec.slides[0], "code"), "구형 평면 필드가 남아 있다"


def test_legacy_flat_slide_is_normalized() -> None:
    """DB에 저장된 구형 media_spec을 재렌더할 수 있어야 한다(FR-G3)."""
    from sns.render.video.square_spec import CodeSquare

    spec = parse_video_spec(
        {
            **MINIMAL,
            "slides": [
                {
                    "subtitle": "왜 느린가",
                    "narration": "in 연산자는 처음부터 끝까지 훑습니다.",
                    "code": "x in xs",
                    "lang": "python",
                    "focus_lines": [1],
                }
            ],
        }
    )
    assert spec.slides[0].square == CodeSquare(text="x in xs", lang="python", focus=(1,))


def test_legacy_image_fields_are_normalized() -> None:
    from sns.render.video.square_spec import ImageSquare

    spec = parse_video_spec(
        {
            **MINIMAL,
            "slides": [
                {
                    "subtitle": "서버실",
                    "narration": "데이터센터는 이렇게 생겼습니다.",
                    "image_query": "server rack",
                    "image_ref": "mem://a.png",
                }
            ],
        }
    )
    assert spec.slides[0].square == ImageSquare(query="server rack", ref="mem://a.png")


def test_mixing_legacy_and_new_shape_is_rejected() -> None:
    """둘 다 쓰면 어느 쪽이 이기는지 알 수 없다 — 조용히 하나를 버리지 않는다."""
    with pytest.raises(VideoSpecError, match="square"):
        parse_video_spec(
            {
                **MINIMAL,
                "slides": [
                    {
                        "subtitle": "부제",
                        "narration": "한 문장입니다.",
                        "code": "print(1)",
                        "square": {"source": "code", "text": "print(2)"},
                    }
                ],
            }
        )
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/test_video_spec.py -q -k "square or legacy"`
Expected: FAIL — `Slide.__init__() got an unexpected keyword argument 'square'` 또는 `VideoSpecError: 'slides[0]'의 square가 모르는 필드`

- [ ] **Step 3: 구현한다**

`sns/render/video/spec.py`에서:

1. `Slide` 정의를 아래로 교체한다.

```python
@dataclass(frozen=True)
class Slide:
    """화면 1장 = 컷 1개. 부제·정사각·나레이션이 컷마다 바뀐다.

    정사각에 무엇이 들어가는지는 `square` 하나로 표현한다 — 도메인마다 달라지는 유일한
    자리라, 평평한 필드로 두면 안 쓰는 도메인도 그 어휘를 지고 다닌다.
    """

    subtitle: str
    narration: str
    square: SquarePayload | None = None
```

2. 구형 정규화 함수를 추가한다.

```python
# 구형 평면 필드 → 신형 square 페이로드. DB에 저장된 media_spec(FR-G3)을 재렌더하려면
# 읽을 수 있어야 한다. **쓰기는 신형만 한다** — 에이전트 프롬프트가 신형만 안내한다.
_LEGACY_CODE = ("code", "lang", "focus_lines")
_LEGACY_IMAGE = ("image_query", "image_prompt", "image_ref")


def normalize_legacy_slide(raw: Mapping[str, object]) -> dict[str, object]:
    """구형 슬라이드를 신형으로 정규화한다. 신형이면 그대로 돌려준다."""
    out = {k: v for k, v in raw.items()}
    legacy = [k for k in (*_LEGACY_CODE, *_LEGACY_IMAGE, "concept") if raw.get(k)]
    if not legacy:
        return out
    if "square" in raw:
        raise VideoSpecError(
            f"구형 필드({legacy})와 'square'를 함께 쓸 수 없음 — 어느 쪽이 이기는지 "
            "정할 수 없다. 'square' 하나로 쓰세요"
        )
    if raw.get("code"):
        out["square"] = {
            "source": "code",
            "text": raw["code"],
            "lang": raw.get("lang", ""),
            "focus": list(raw.get("focus_lines", ())),  # type: ignore[call-overload]
        }
    elif raw.get("concept"):
        concept = raw["concept"]
        if not isinstance(concept, Mapping):
            raise VideoSpecError(f"'concept'는 매핑이어야 함: {concept!r}")
        out["square"] = {"source": "concept", **concept}
    else:
        out["square"] = {
            "source": "image",
            "query": raw.get("image_query", ""),
            "prompt": raw.get("image_prompt", ""),
            "ref": raw.get("image_ref", ""),
        }
    for key in (*_LEGACY_CODE, *_LEGACY_IMAGE, "concept"):
        out.pop(key, None)
    return out
```

3. `_parse_slide`를 아래로 교체한다. 기존 `_parse_focus`·`_parse_image_text`·`_parse_concept`·`_reject_unused_square_fields`·`_optional_str`는 **삭제**한다(Task 1이 그 역할을 흡수했다).

```python
def _parse_slide(
    raw: object, index: int, kinds: tuple[str, ...], sources: tuple[str, ...]
) -> Slide:
    where = f"'slides[{index}]'의 "
    if not isinstance(raw, Mapping):
        raise VideoSpecError(f"'slides[{index}]'는 매핑이어야 함: {raw!r}")
    normalized = normalize_legacy_slide(raw)
    square_raw = normalized.get("square")
    square: SquarePayload | None = None
    if square_raw is not None:
        if not isinstance(square_raw, Mapping):
            raise VideoSpecError(f"{where}'square'는 매핑이어야 함: {square_raw!r}")
        try:
            square = parse_square(square_raw, where, sources=sources, kinds=kinds)
        except SquareSpecError as exc:
            raise VideoSpecError(str(exc)) from exc
    return Slide(
        subtitle=_require_text(normalized, "subtitle", where, MAX_SUBTITLE_WIDTH),
        narration=_require_text(normalized, "narration", where, MAX_NARRATION_WIDTH),
        square=square,
    )
```

4. `_reject_generated_images_in_code_videos`를 페이로드 기반으로 바꾼다.

```python
def _reject_generated_images_in_code_videos(slides: tuple[Slide, ...]) -> None:
    """코드가 한 컷이라도 있으면 생성 이미지를 영상 전체에서 거부한다(근거는 §1.4)."""
    code_cuts = [i for i, s in enumerate(slides) if isinstance(s.square, CodeSquare) and s.square.text.strip()]
    prompt_cuts = [i for i, s in enumerate(slides) if isinstance(s.square, ImageSquare) and s.square.prompt]
    if code_cuts and prompt_cuts:
        raise VideoSpecError(
            "코드가 있는 영상에서는 생성 이미지를 쓸 수 없음 — 개념 그림(concept)을 쓰세요. "
            f"코드: {', '.join(f'slides[{i}]' for i in code_cuts)} / "
            f"생성 요청: {', '.join(f'slides[{i}]' for i in prompt_cuts)}"
        )
```

5. import를 정리한다. `SQUARE_FIELDS`와 `MAX_CODE_LINES`·`MAX_IMAGE_QUERY_LEN`·`MAX_IMAGE_PROMPT_LEN`는 `square_spec`으로 옮겨갔으므로 삭제하고, 대신:

```python
from sns.render.video.square_spec import (
    CodeSquare,
    ImageSquare,
    SquarePayload,
    SquareSpecError,
    parse_square,
)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/test_video_spec.py -q`
Expected: 새 테스트 4개는 PASS. **기존 테스트 다수가 FAIL한다** — 구형 필드를 직접 단언하기 때문이다. 이는 예상된 것이며 Step 5에서 고친다.

- [ ] **Step 5: 기존 테스트 픽스처를 신형으로 옮긴다**

`tests/test_video_spec.py`에서 슬라이드 딕셔너리에 `code`/`lang`/`focus_lines`/`concept`/`image_*`를 직접 쓰는 곳을 `"square": {...}` 형태로 바꾼다. **단언은 약화시키지 않는다** — 예를 들어 `assert spec.slides[0].code == "x"`는 `assert spec.slides[0].square == CodeSquare(text="x")`가 된다.

구형 경로를 검증하는 테스트는 Step 1에서 이미 추가했으므로, 나머지는 전부 신형으로 옮긴다.

- [ ] **Step 6: 네 단계를 돌린다**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy sns && uv run pytest -q`
Expected: `tests/test_video_render.py`·`test_image_resolve.py`·`test_cycle_runner.py`가 아직 실패한다(Task 3·4에서 고친다). `test_video_spec.py`와 `test_square_spec.py`는 전부 통과해야 한다.

- [ ] **Step 7: 커밋**

```bash
git add sns/render/video/spec.py tests/test_video_spec.py
git commit -m "refactor(render): Slide.square 로 전환 + 구형 스펙 정규화

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

> **주의:** 이 커밋 시점에는 전체 테스트가 초록이 아니다. Task 2~4는 한 덩어리로 봐야 하는 스키마 전환이고, 중간 커밋에서 초록을 만들려면 어댑터를 두 벌 유지해야 해서 오히려 위험하다. **Task 4를 마치기 전에는 push하지 않는다.**

---

### Task 3: 렌더러를 페이로드 기반으로

**Files:**
- Modify: `sns/render/video/renderer.py`
- Test: `tests/test_video_render.py`

**Interfaces:**
- Consumes: Task 2의 `Slide.square`, Task 1의 `CodeSquare`·`ConceptSquare`·`ImageSquare`
- Produces: 없음(내부 함수만 변경). `_square(slide, side, spec, mono_path, font_path, fetch_image)` 시그니처 유지.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_video_render.py 에 추가
def test_renderer_draws_code_from_the_square_payload() -> None:
    """페이로드 타입으로 분기한다 — 슬라이드가 평면 필드를 안 들고 다닌다."""
    from sns.render.video.square_spec import CodeSquare

    spec = parse_video_spec(
        {
            "topic": "리스트 대신 셋",
            "slides": [
                {
                    "subtitle": "왜 느린가",
                    "narration": "리스트는 처음부터 끝까지 훑습니다.",
                    "square": {"source": "code", "text": "x in xs", "lang": "python"},
                }
            ],
        }
    )
    assert isinstance(spec.slides[0].square, CodeSquare)
    render = render_video(spec, synthesize=tone_wav)
    assert render.mp4[:4] and render.duration_s > 0
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/test_video_render.py::test_renderer_draws_code_from_the_square_payload -q`
Expected: FAIL — `AttributeError: 'Slide' object has no attribute 'code'`

- [ ] **Step 3: `_square`를 페이로드 분기로 바꾼다**

```python
    for source in spec.square_sources:
        payload = slide.square
        if source == "code" and isinstance(payload, CodeSquare) and payload.text.strip():
            # 지연 import — 코드를 쓰지 않는 도메인이 pygments를 물지 않게 한다.
            from sns.render.code_image import render_code_square

            png = render_code_square(
                payload.text, lang=payload.lang or None, size=side,
                focus_lines=payload.focus, mono_path=mono_path, font_path=font_path,
            )  # fmt: skip
            return Image.open(io.BytesIO(png)).convert("RGB")
        if source == "concept" and isinstance(payload, ConceptSquare):
            png = render_concept_square(
                payload.concept, size=side, font_path=font_path, mono_path=mono_path
            )
            return Image.open(io.BytesIO(png)).convert("RGB")
        if source == "image" and isinstance(payload, ImageSquare) and payload.ref:
            if fetch_image is None:
                raise VideoSpecError(
                    f"'square.ref'({payload.ref})가 있는데 fetch_image seam이 없음 — "
                    "조용히 그라데이션으로 떨어지지 않는다"
                )
            photo = Image.open(io.BytesIO(fetch_image(payload.ref))).convert("RGB")
            if photo.size != (side, side):
                photo = photo.resize((side, side), Image.Resampling.LANCZOS)
            return photo
        if source == "gradient":
            break
    return _gradient(side, side, spec.background, spec.background2)
```

import에 `from sns.render.video.square_spec import CodeSquare, ConceptSquare, ImageSquare`를 더한다.

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/test_video_render.py -q`
Expected: 새 테스트 PASS. 기존 테스트 중 구형 픽스처를 쓰는 것은 실패하므로 Step 5에서 옮긴다.

- [ ] **Step 5: 기존 렌더 테스트 픽스처를 옮긴다**

`tests/test_video_render.py`의 슬라이드 딕셔너리를 `"square": {...}`로 바꾼다. 픽셀 단언(진행바·액센트 바·프레임 수)은 **그대로 둔다** — 이 리팩터는 렌더 결과를 바꾸지 않아야 한다.

- [ ] **Step 6: 결정론을 확인한다**

Run: `uv run pytest tests/test_video_render.py -q -k deterministic`
Expected: PASS. 같은 spec을 두 번 렌더해 checksum이 같아야 한다(`FR-M1`).

- [ ] **Step 7: 커밋**

```bash
git add sns/render/video/renderer.py tests/test_video_render.py
git commit -m "refactor(render): 렌더러가 정사각 페이로드 타입으로 분기

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: 이미지 해소를 square.ref 로

**Files:**
- Modify: `sns/render/images/resolve.py`
- Modify: `sns/render/images/credit.py` — `image_source`·`image_credit`을 읽으므로 함께 옮긴다
- Test: `tests/test_image_resolve.py`, `tests/test_image_credit.py`

**Interfaces:**
- Consumes: Task 2의 신형 슬라이드 스키마
- Produces: `resolve_images`가 `slide["square"]["ref"]`에 쓴다. 크레딧은 `slide["square"]["source_url"]`·`slide["square"]["credit"]`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_image_resolve.py 에 추가
def test_resolution_writes_into_the_square_payload() -> None:
    store = InMemoryMediaStore()
    spec = {
        "topic": "서버실",
        "slides": [
            {
                "subtitle": "데이터센터",
                "narration": "서버가 늘어선 방입니다.",
                "square": {"source": "image", "query": "server rack"},
            }
        ],
    }
    out = resolve_images(spec, store=store, search=_one_hit, download=_png_bytes)
    square = out.media_spec["slides"][0]["square"]
    assert square["ref"].startswith("mem://")
    assert square["query"] == "server rack", "질의는 감사용으로 남긴다"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/test_image_resolve.py::test_resolution_writes_into_the_square_payload -q`
Expected: FAIL — `KeyError: 'square'`

- [ ] **Step 3: `_resolve_slide`를 페이로드 기반으로 바꾼다**

`sns/render/images/resolve.py`에서 `slide.get("image_ref")`·`slide.get("image_prompt")`·`slide.get("image_query")`·`slide["image_ref"] = ...`를 전부 `square` 하위로 옮긴다.

```python
def _resolve_slide(
    slide: dict[str, object],
    where: str,
    *,
    store: MediaStore,
    search: SearchImages,
    download: DownloadImage,
    generate: GenerateImage | None,
    dim: float,
) -> str | None:
    """사진을 붙였으면 None, 못 붙였으면 사유 문자열."""
    payload = slide.get("square")
    if not isinstance(payload, dict) or payload.get("source") != "image":
        return None  # 이 컷은 사진 자리가 아니다
    if payload.get("ref"):
        return None  # 이미 못박혀 있다(재실행 멱등)

    # 생성이 스톡보다 앞이다 — 직접 말한 구도가 검색어보다 정확하다.
    prompt = str(payload.get("prompt", "")).strip()
    if prompt:
        if generate is None:
            return f"{where} square.prompt가 있으나 generate가 미배선(유료) — 그림 생략"
        verdict = screen_query(prompt)
        if not verdict.allowed:
            return f"{where} {verdict.reason}"
        try:
            image = to_square(generate(prompt), dim=dim)
        except (ImageGenerationError, ImageSourceError, OSError) as exc:
            return f"{where} 이미지 생성 실패 — {exc}"
        # 우리가 만든 그림이라 출처 표기가 없다 — Pexels 크레딧을 달면 거짓 표기가 된다.
        payload["ref"] = store.put(
            image, checksum=hashlib.sha256(image).hexdigest(), kind="image", ext="png"
        )
        return None

    query = str(payload.get("query", "")).strip()
    if not query:
        return None  # 붙일 게 없다
    # 게이트를 여기서도 건다. `search_pexels`도 검열하지만 그건 어댑터 사정이라,
    # 소스를 갈아끼우면 게이트가 통째로 빠진다 — 금지 소재 차단이 주입 대상이면 안 된다.
    verdict = screen_query(query)
    if not verdict.allowed:
        return f"{where} {verdict.reason}"
    try:
        picked = pick_image(search(query, limit=DEFAULT_LIMIT))
        if picked is None:
            return f"{where} 게이트를 통과한 후보 없음 — 질의 {query!r}"
        image = to_square(download(picked.download_url), dim=dim)
    except (PexelsError, ImageSourceError, OSError) as exc:
        return f"{where} 사진 해소 실패 — {exc}"

    checksum = hashlib.sha256(image).hexdigest()
    payload["ref"] = store.put(image, checksum=checksum, kind="image", ext="png")
    # 출처는 spec(jsonb)에 남긴다 — 저작권 근거를 사후에 확인할 수 있어야 하고(FR-Q7),
    # 캡션의 크레딧 줄도 여기서 나온다([sns.render.images.credit]).
    payload["source_url"] = picked.page_url
    payload["credit"] = picked.photographer
    return None
```

지역 변수 이름을 `square` → `image`로 바꾼 데 주의한다. 페이로드 딕셔너리가 `payload`를
차지하므로, 정사각으로 자른 이미지 바이트는 다른 이름이어야 한다.

**`credit.py`도 함께 고친다** — 지금 `slide["image_source"]`·`slide["image_credit"]`을
읽는다. `slide["square"]["source_url"]`·`["credit"]`으로 바꾼다:

```python
# sns/render/images/credit.py — image_credits() 안
        payload = slide.get("square")
        if not isinstance(payload, Mapping):
            continue
        page_url = str(payload.get("source_url", "")).strip()
        if not page_url:
            continue
        out.append(
            ImageCredit(photographer=str(payload.get("credit", "")).strip(), page_url=page_url)
        )
```

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/test_image_resolve.py -q`
Expected: 새 테스트 PASS. 기존 테스트는 Step 5에서 옮긴다.

- [ ] **Step 5: 나머지 테스트 픽스처를 옮긴다**

`tests/test_image_resolve.py`·`tests/test_image_credit.py`·`tests/test_cycle_runner.py`·`tests/test_domain.py`·`tests/test_content_agent.py`·`scripts/e2e_youtube_shorts.py`의 슬라이드 딕셔너리를 신형으로 바꾼다.

- [ ] **Step 6: 전체 네 단계**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy sns && uv run pytest -q`
Expected: **전부 통과.** 여기서 처음으로 전체가 초록이 된다.

- [ ] **Step 7: 커밋하고 push**

```bash
git add -A
git commit -m "refactor(images): 사진 해소를 square 페이로드 안으로

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git push
```

---

### Task 5: 프롬프트를 신형 스키마로

**Files:**
- Modify: `sns/domain/developer.py`
- Test: `tests/test_domain.py`

**Interfaces:**
- Consumes: Task 1~4의 신형 스키마
- Produces: 없음(팩 값만 변경)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_domain.py 에 추가
def test_developer_square_guidance_uses_the_new_schema() -> None:
    """프롬프트가 구형 평면 필드를 안내하면 에이전트가 구형 spec을 낸다."""
    guidance = DEVELOPER.square_guidance
    assert '"source"' in guidance
    for legacy in ("focus_lines", "image_query", "image_prompt"):
        assert legacy not in guidance, f"구형 필드 안내가 남아 있다: {legacy}"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/test_domain.py::test_developer_square_guidance_uses_the_new_schema -q`
Expected: FAIL — `구형 필드 안내가 남아 있다: focus_lines`

- [ ] **Step 3: `_SQUARE_GUIDANCE`를 고친다**

`sns/domain/developer.py`의 `_SQUARE_GUIDANCE`에서 평면 필드 안내를 `square` 하나로 바꾼다. 예시도 함께 바꾼다:

```
     * square(선택): 가운데 정사각에 넣을 것. source로 종류를 정한다.
       한 컷에 하나만 — 정사각은 하나다. 마땅치 않으면 통째로 비워라.
       - 코드: {"source":"code","text":"x in xs","lang":"python","focus":[1]}
         text는 최대 18줄, 한 줄은 48자 이내 권장. focus는 지금 말하는 줄(1-기반).
       - 개념 그림: {"source":"concept","kind":"emphasis","tag":"최악의 경우",
         "headline":"100억","sub":"십만 건 × 십만 건 비교"}
         종류 «N»개뿐이고 다른 kind나 없는 필드를 쓰면 거부된다.
«EXAMPLES»
       - 실사 사진: {"source":"image","query":"server rack"}
         **물리적 대상**일 때만. 추상 개념에는 절대 쓰지 마라 — 전에 "list vs set" 컷에
         전선 사진이 붙었다. 개념은 concept이 맡는다.
```

`content.py`의 슬라이드 스키마 요약도 `{"subtitle","narration","square"}`로 맞춘다.

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/test_domain.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add sns/domain/developer.py sns/agents/content.py tests/test_domain.py
git commit -m "refactor(domain): 정사각 안내를 신형 square 스키마로

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: 구형 호환의 제거 조건을 문서화

**Files:**
- Modify: `docs/plan/15-구현-이탈기록.md`
- Modify: `sns/render/video/spec.py` (docstring만)

**Interfaces:** 없음

- [ ] **Step 1: 이탈 기록에 항목을 더한다**

`§3` 아래에 추가한다:

```markdown
### 3.3 구형 평면 슬라이드 스펙 호환

**왜 남아 있나**: `media_spec`은 `content_item`에 저장되고 재렌더 입력이 된다(`FR-G3`).
이미 발행된 콘텐츠의 spec은 전부 구형(평면 `code`·`image_query` …)이라, 파서가 읽지
못하면 과거 콘텐츠를 다시 렌더할 수 없다.

**제거 조건**: `content_item` 중 구형 스펙을 가진 행이 0이 되면 `normalize_legacy_slide`와
그 테스트를 지운다. 확인 쿼리:

    SELECT count(*) FROM content_item
     WHERE media_spec -> 'slides' @> '[{"code": {}}]'
        OR media_spec -> 'slides' @> '[{"image_query": {}}]';

**왜 지금 안 지우나**: 발행 이력이 아직 짧아 며칠이면 0이 될 수도 있지만, 0인지 확인할
DB 접근이 개발 환경마다 다르다. 조건을 적어두고 확인은 운영자에게 맡긴다.
```

- [ ] **Step 2: `spec.py`의 `normalize_legacy_slide` docstring에 제거 조건을 적는다**

```python
    """구형 슬라이드를 신형으로 정규화한다. 신형이면 그대로 돌려준다.

    제거 조건은 [docs/plan/15-구현-이탈기록.md] §3.3에 있다 — 구형 스펙을 가진
    `content_item` 행이 0이 되면 이 함수와 테스트를 지운다.
    """
```

- [ ] **Step 3: 링크를 검사한다**

Run:
```bash
uv run python -c "
import re, pathlib
bad=[]
for md in pathlib.Path('docs').rglob('*.md'):
    t=md.read_text(encoding='utf-8')
    for m in re.finditer(r'\[[^\]]*\]\(([^)#][^)]*?)(?:#[^)]*)?\)', t):
        g=m.group(1)
        if g.startswith(('http://','https://','mailto:')): continue
        if not (md.parent/g).exists(): bad.append(f'{md} -> {g}')
print('broken:', len(bad), bad)
"
```
Expected: `broken: 0 []`

- [ ] **Step 4: 커밋**

```bash
git add docs/plan/15-구현-이탈기록.md sns/render/video/spec.py
git commit -m "docs: 구형 슬라이드 스펙 호환의 제거 조건 명시

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: 어휘가 실제로 사라졌는지 강제

**Files:**
- Modify: `tests/test_render_layering.py`

**Interfaces:** 없음

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_render_layering.py 에 추가
def test_slide_has_no_developer_vocabulary() -> None:
    """Slide 필드에 도메인 어휘가 다시 새는 걸 막는다.

    이 리팩터의 목적이 그것이므로, 목적 자체를 테스트로 못박는다. 구형 호환은
    `normalize_legacy_slide` 안에만 있어야 한다.
    """
    import dataclasses

    from sns.render.video.spec import Slide

    names = {f.name for f in dataclasses.fields(Slide)}
    assert names == {"subtitle", "narration", "square"}, f"예상 밖 필드: {names}"
```

- [ ] **Step 2: 실패를 확인한다** (Task 2 이전이라면 실패, 이후라면 통과)

Run: `uv run pytest tests/test_render_layering.py -q`
Expected: Task 2~4를 마쳤다면 PASS

- [ ] **Step 3: 전체 네 단계**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy sns && uv run pytest -q`
Expected: 전부 통과

- [ ] **Step 4: 커밋하고 push**

```bash
git add tests/test_render_layering.py
git commit -m "test: Slide 필드에 도메인 어휘가 다시 새지 않게 강제

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git push
```

---

## 실행 순서와 위험

**Task 2~4는 한 덩어리다.** 스키마 전환이라 중간에 전체 초록을 만들려면 어댑터를 두 벌 유지해야 하고, 그게 더 위험하다. **Task 4 Step 6에서 처음으로 전체가 초록이 되고, 그 전에는 push하지 않는다.**

| 위험 | 대응 |
|---|---|
| 렌더 결과가 바뀐다 | Task 3 Step 6의 결정론 테스트. 픽셀 단언을 약화시키지 않는다 |
| DB의 구형 spec을 못 읽는다 | Task 2의 `normalize_legacy_slide` + 전용 테스트 3개 |
| 에이전트가 구형 spec을 계속 낸다 | Task 5가 프롬프트를 바꾼다. Task 5를 빠뜨리면 구형 경로만 계속 쓰인다 |
| 어휘가 슬금슬금 돌아온다 | Task 7이 `Slide` 필드 집합을 못박는다 |

**예상 소요**: Task 1 40분 · Task 2 90분 · Task 3 40분 · Task 4 60분 · Task 5 20분 · Task 6 20분 · Task 7 10분 — 합계 **약 5시간**. 테스트 픽스처 이관(Task 2 Step 5, Task 4 Step 5)이 절반을 차지한다.

**착수 조건**: 27일 MVP 마감 이후. 이 리팩터는 동작을 바꾸지 않으므로 급하지 않고, 마감 전 스키마 전환은 발행 파이프라인을 흔든다.
