"""발행 전 카드 품질 게이트 (FR-Q1·Q2·Q4, FR-A2 카드 항목).

렌더된 카드가 발행 파이프라인에 진입하기 전 규격·가독성 하한을 강제한다. 미통과=발행
차단(상태머신 C5의 `quality_passed=False`). 판정은 순수 함수 + 코드 상수 임계값
(FR-Q4) — 같은 자산·같은 기준 → 같은 판정(결정론 재검사).

`QualityReport`는 media_asset의 `quality_status`(passed/failed) + `quality_report`
(jsonb)에 그대로 실린다(FR-Q2). needs_review는 hybrid 사람 관문(FR-Q3)의 몫이라
자동 게이트는 passed/failed만 낸다.

FR-A2의 해상도<1080×1920·음소거·안전영역 검사 중 음소거/영상 해상도는 영상(C4)
소관이라 여기선 카드에 해당하는 항목만 — 해상도 하한·안전영역(overflow)·직전 N건
콘텐츠 유사도 — 을 본다.
"""

from dataclasses import dataclass
from typing import Literal

from sns.render.card.renderer import CardRender
from sns.render.card.spec import CardSpec

QualityStatus = Literal["passed", "failed", "needs_review"]

# ── 임계값 (FR-Q4: 코드 상수로 외부화, 프롬프트 아님) ──────────────────
# WCAG AA 본문 대비 하한.
MIN_CONTRAST_RATIO = 4.5
# 카드 이미지 최소 변 길이(px). 영상 1080×1920 하한은 C4 소관.
MIN_CARD_SIDE = 1080
# 직전 N건과의 콘텐츠 토큰 Jaccard 상한 — 초과 시 near-duplicate(NFR-11).
MAX_CONTENT_SIMILARITY = 0.8


@dataclass(frozen=True)
class QualityCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class QualityReport:
    status: QualityStatus
    checks: tuple[QualityCheck, ...]

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_json(self) -> dict[str, object]:
        """media_asset.quality_report(jsonb) 적재용."""
        return {
            "status": self.status,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks
            ],
        }


# ── 순수 계산 헬퍼 ────────────────────────────────────────────────────


def _srgb_to_linear(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _srgb_to_linear(r) + 0.7152 * _srgb_to_linear(g) + 0.0722 * _srgb_to_linear(b)


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """WCAG 대비비 (1.0~21.0). 순서 무관."""
    lighter, darker = sorted(
        (_relative_luminance(fg_hex), _relative_luminance(bg_hex)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def content_signature(spec: CardSpec) -> frozenset[str]:
    """카드 콘텐츠의 토큰 집합 — 직전 N건 유사도 비교의 지문.

    팔레트·규격은 브랜드 템플릿상 항상 같으므로 제외하고, 실제 텍스트(훅·제목·본문·
    푸터)의 토큰만 본다 — 같은 내용 재게시(어그리게이터 판정 방어)를 잡는 신호.
    """
    text = " ".join((spec.hook, spec.title, *spec.body, spec.footer)).lower()
    return frozenset(text.split())


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def max_similarity(signature: frozenset[str], recent: tuple[frozenset[str], ...]) -> float:
    return max((_jaccard(signature, r) for r in recent), default=0.0)


# ── 게이트 ────────────────────────────────────────────────────────────


def check_card(
    spec: CardSpec,
    render: CardRender,
    *,
    recent_signatures: tuple[frozenset[str], ...] = (),
) -> QualityReport:
    """카드 자산의 발행 가능 여부를 판정. 하나라도 실패 → status=failed."""
    checks: list[QualityCheck] = []

    # 필수 필드 (parse가 이미 강제하지만 자산 수준 재확인).
    missing = [
        name
        for name, value in (("hook", spec.hook), ("title", spec.title), ("footer", spec.footer))
        if not value.strip()
    ] + ([] if any(line.strip() for line in spec.body) else ["body"])
    checks.append(
        QualityCheck(
            "required_fields",
            not missing,
            "필수 필드 present" if not missing else f"누락: {', '.join(missing)}",
        )
    )

    # 텍스트 오버플로우 (FR-Q1: 안전영역 초과 금지).
    checks.append(
        QualityCheck(
            "text_overflow",
            not render.overflow,
            "안전영역 내 배치" if not render.overflow else "텍스트가 안전영역을 넘침",
        )
    )

    # 최소 대비 (FR-Q1: 가독성 하한).
    ratio = contrast_ratio(spec.palette.foreground, spec.palette.background)
    checks.append(
        QualityCheck(
            "min_contrast",
            ratio >= MIN_CONTRAST_RATIO,
            f"대비비 {ratio:.2f} (하한 {MIN_CONTRAST_RATIO})",
        )
    )

    # 해상도 하한 (FR-A2 카드 항목).
    min_side = min(render.width, render.height)
    checks.append(
        QualityCheck(
            "min_resolution",
            min_side >= MIN_CARD_SIDE,
            f"{render.width}×{render.height} (최소 변 하한 {MIN_CARD_SIDE})",
        )
    )

    # 직전 N건 콘텐츠 유사도 (FR-A2: near-duplicate 방어, NFR-11).
    similarity = max_similarity(content_signature(spec), recent_signatures)
    checks.append(
        QualityCheck(
            "content_similarity",
            similarity <= MAX_CONTENT_SIMILARITY,
            f"직전 N건 최대 유사도 {similarity:.2f} (상한 {MAX_CONTENT_SIMILARITY})",
        )
    )

    status: QualityStatus = "passed" if all(c.passed for c in checks) else "failed"
    return QualityReport(status=status, checks=tuple(checks))
