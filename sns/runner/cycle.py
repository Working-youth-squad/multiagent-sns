"""사이클 오케스트레이터 (FR-C1·C5, 03 §2) — 한 사이클을 기획→제작→적재까지 구동.

deepagents 서브에이전트(Topic·Content)와 동결 계약(research_trends·render_media)을
CycleStore seam 위에서 엮어, **한 주제로 여러 채널 대상의 콘텐츠를 만들고 발행 대기
(publication.pending)까지** 만든다. 실제 발행은 여기서 하지 않는다 — 기존
`run_pending_publications`(sns.publish.runner)가 pending 원장을 종결까지 전진시킨다.
"무인 관통"은 이 둘의 합성이다(run_cycle → run_pending_publications).

설계:
- **주제는 사이클당 1건**(01 통제변수: 동일 주제 도메인 공유). read_stats는 대표
  플랫폼(targets[0]) 기준으로 읽는다.
- **대상별 격리**(FR-P4 정신): 한 대상의 콘텐츠 생성/렌더 실패는 error 이벤트로
  기록하고 다음 대상으로 넘어간다 — 사이클 전체를 죽이지 않는다.
- **품질 게이트는 주입식**(assess_quality): 게이트가 렌더러 내부(CardRender)를 봐야
  하므로 얇은 RenderMedia 계약으론 못 부른다 → 조립은 caller 몫. 미주입 시 자산은
  needs_review로 적재(사람 관문/후속 게이트 대기, FR-Q3).
- LLM 착지점(FR-C4)은 content_item.body 하나뿐 — topic/media_spec/hook은 코드가 검증.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from langchain_core.language_models import BaseChatModel

from sns.agents.content import ContentRejected, run_content
from sns.agents.topic import TopicResult, TopicSelectionError, run_topic
from sns.quality.gate import QualityReport
from sns.runner.store import CycleStore
from sns.tools.contracts import (
    ContentFormat,
    MediaAsset,
    MediaKind,
    Platform,
    ReadStats,
    RenderMedia,
    ResearchTrends,
)

ChannelMode = Literal["auto", "hybrid"]
TargetOutcome = Literal["prepared", "failed"]

# 포맷 → 렌더 자산 종류. 피드=이미지, 릴스/쇼츠=영상.
_FORMAT_KIND: dict[ContentFormat, MediaKind] = {
    "feed_image": "image",
    "reels": "video",
    "shorts": "video",
}


class AssessQuality(Protocol):
    """렌더 자산의 품질 판정(주입식). caller가 포맷별 게이트(check_card 등)를 배선한다."""

    def __call__(
        self, *, media_spec: Mapping[str, object], media: MediaAsset, content_format: ContentFormat
    ) -> QualityReport: ...


@dataclass(frozen=True)
class CycleTarget:
    channel_id: str
    platform: Platform
    content_format: ContentFormat
    mode: ChannelMode  # auto → 초안 approved, hybrid → needs_review(사람 관문)


@dataclass(frozen=True)
class TargetResult:
    channel_id: str
    outcome: TargetOutcome
    content_item_id: str | None = None
    media_asset_id: str | None = None
    publication_id: str | None = None
    error: str | None = None  # 실패 사유 원문(관측·디버깅)


@dataclass(frozen=True)
class CycleResult:
    cycle_id: str
    status: Literal["completed", "failed"]
    topic_id: str | None
    targets: tuple[TargetResult, ...]

    @property
    def prepared(self) -> tuple[TargetResult, ...]:
        return tuple(t for t in self.targets if t.outcome == "prepared")


def run_cycle(
    store: CycleStore,
    *,
    goal_ref: str,
    targets: Sequence[CycleTarget],
    model: BaseChatModel,
    research_trends: ResearchTrends,
    read_stats: ReadStats,
    render_media: RenderMedia,
    assess_quality: AssessQuality | None = None,
    playbook_guidance: str | None = None,
) -> CycleResult:
    """한 사이클 구동. 주제 확정 실패면 status=failed로 종결, 대상 실패는 격리."""
    if not targets:
        raise ValueError("targets가 비어 있음 — 발행 대상 채널이 필요")

    cycle_id = store.create_cycle(goal_ref)
    store.log_event(
        cycle_id=cycle_id,
        kind="cycle_started",
        payload={"goal_ref": goal_ref, "target_count": len(targets)},
    )

    # ── 주제(사이클당 1건, 통제변수: 동일 주제 도메인) ──────────────────
    try:
        topic = run_topic(
            model,
            platform=targets[0].platform,
            research_trends=research_trends,
            read_stats=read_stats,
        )
    except TopicSelectionError as exc:
        store.log_event(
            cycle_id=cycle_id, kind="error", payload={"stage": "topic", "reason": str(exc)}
        )
        store.complete_cycle(cycle_id, status="failed")
        return CycleResult(cycle_id=cycle_id, status="failed", topic_id=None, targets=())

    topic_id = store.save_topic(title=topic.title, summary=topic.summary, source=topic.source)
    store.log_event(
        cycle_id=cycle_id,
        kind="agent_called",
        payload={"agent": "topic", "topic_id": topic_id, "category": topic.category},
    )

    # ── 대상별 콘텐츠 제작·적재(격리) ─────────────────────────────────
    results: list[TargetResult] = []
    for target in targets:
        try:
            results.append(
                _prepare_target(
                    store,
                    cycle_id=cycle_id,
                    topic_id=topic_id,
                    topic=topic,
                    target=target,
                    model=model,
                    render_media=render_media,
                    assess_quality=assess_quality,
                    playbook_guidance=playbook_guidance,
                )
            )
        except (ContentRejected, ValueError, RuntimeError) as exc:
            store.log_event(
                cycle_id=cycle_id,
                kind="error",
                payload={"stage": "content", "channel_id": target.channel_id, "reason": str(exc)},
            )
            results.append(
                TargetResult(channel_id=target.channel_id, outcome="failed", error=str(exc))
            )

    store.complete_cycle(cycle_id, status="completed")
    store.log_event(
        cycle_id=cycle_id,
        kind="cycle_completed",
        payload={"prepared": sum(t.outcome == "prepared" for t in results), "total": len(results)},
    )
    return CycleResult(
        cycle_id=cycle_id, status="completed", topic_id=topic_id, targets=tuple(results)
    )


def _prepare_target(
    store: CycleStore,
    *,
    cycle_id: str,
    topic_id: str,
    topic: TopicResult,
    target: CycleTarget,
    model: BaseChatModel,
    render_media: RenderMedia,
    assess_quality: AssessQuality | None,
    playbook_guidance: str | None,
) -> TargetResult:
    fmt = target.content_format

    content = run_content(
        model, topic=topic, content_format=fmt, playbook_guidance=playbook_guidance
    )
    store.log_event(
        cycle_id=cycle_id,
        kind="agent_called",
        payload={"agent": "content", "channel_id": target.channel_id, "hook": content.hook_pattern},
    )

    kind = _FORMAT_KIND[fmt]
    media = render_media(content.media_spec, kind)
    store.log_event(
        cycle_id=cycle_id,
        kind="tool_called",
        payload={"tool": "render_media", "kind": kind, "checksum": media.checksum},
    )

    if assess_quality is not None:
        report = assess_quality(media_spec=content.media_spec, media=media, content_format=fmt)
        quality_status = report.status
        quality_report: Mapping[str, object] | None = report.to_json()
    else:
        # 게이트 미배선 → 자동 발행 보류. 사람 관문/후속 게이트가 passed로 승격(FR-Q3).
        quality_status, quality_report = "needs_review", None

    # 콘텐츠 → 자산 → 발행 대기 (FK 순서). auto=approved, hybrid=사람 관문 대기.
    content_item_id = store.save_content_item(
        cycle_id=cycle_id,
        topic_id=topic_id,
        content_format=fmt,
        body=content.body,
        media_spec=content.media_spec,
        hook_pattern=content.hook_pattern,
        status="approved" if target.mode == "auto" else "needs_review",
    )
    media_asset_id = store.save_media_asset(
        content_item_id=content_item_id,
        kind=kind,
        storage_url=media.storage_url,
        checksum=media.checksum,
        quality_status=quality_status,
        quality_report=quality_report,
    )
    publication_id = store.create_publication(
        content_item_id=content_item_id, channel_id=target.channel_id
    )
    return TargetResult(
        channel_id=target.channel_id,
        outcome="prepared",
        content_item_id=content_item_id,
        media_asset_id=media_asset_id,
        publication_id=publication_id,
    )
