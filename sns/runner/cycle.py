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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from langchain_core.language_models import BaseChatModel

from sns.agents.content import ContentRejected, run_content
from sns.agents.topic import TopicResult, TopicSelectionError, run_topic
from sns.publish.disclosure import with_ai_disclosure
from sns.publish.modes import DRAFT_STATUS, PublishMode
from sns.quality.gate import QualityReport
from sns.quality.safety import screen_content
from sns.quality.signature import MAX_CONTENT_SIMILARITY, max_similarity, spec_signature
from sns.render.card.spec import CardSpecError
from sns.render.images.credit import with_image_credits
from sns.render.images.resolve import ImageResolution
from sns.render.video.spec import VideoSpecError
from sns.runner.store import CycleStore
from sns.tools.contracts import (
    ContentFormat,
    MediaAsset,
    MediaKind,
    Platform,
    ReadStats,
    RenderMedia,
    ResearchTrends,
    VideoMethod,
)

# 발행 모드 3분류([sns.publish.modes] 정본): auto·hybrid·manual
ChannelMode = PublishMode
# blocked = 관문(안전·근접중복)에 걸려 **렌더도 하지 않고** 사람 검토로 넘긴 것.
# failed(도메인 오류)와 구분한다 — 원고는 멀쩡히 있고 판단만 남았다.
# manual_assigned = 수동 채널에 주제만 배정한 것(FR-E5) — 렌더도 초안도 없다.
TargetOutcome = Literal["prepared", "blocked", "manual_assigned", "failed"]

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
    mode: ChannelMode  # auto → 초안 approved, hybrid → needs_review, manual → 주제 배정만


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


# 주제 이미지 해소 seam — `image_query`를 렌더 전에 `image_ref`로 못박는다
# ([sns.render.images.resolve]). 주입인 이유는 둘이다: 외부 호출·저장소를 러너가 몰라야
# 하고, 테스트가 네트워크 없이 돌아야 한다. 미배선(None)이면 사이클은 그대로 굴러간다.
# 주제 중복 차단 창(일). 트렌드 소스가 같은 항목을 노출하는 기간보다 길게 잡되, 진짜로
# 다시 뜨는 주제는 언젠가 돌아올 수 있게 무한정 막지는 않는다.
RECENT_TOPIC_DAYS = 14
# 근접중복 비교 대상 건수 상한 — 전부 읽으면 사이클마다 비용이 선형으로 는다.
RECENT_SPEC_LIMIT = 50

ResolveMediaSpec = Callable[[Mapping[str, object]], ImageResolution]


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
    resolve_media_spec: ResolveMediaSpec | None = None,
    playbook_guidance: str | None = None,
    topic_major: str,
    supported_methods: Sequence[VideoMethod] = ("template",),
    channel_brief: str | None = None,
    topic_categories: Sequence[str] | None = None,
) -> CycleResult:
    """한 사이클 구동.

    실패 격리 정책(비대칭 — 의도적):
    - **도메인 오류**(콘텐츠 생성 ContentRejected · 렌더 스펙 CardSpecError/VideoSpecError)는
      **대상별로 격리** — 해당 대상만 failed, 나머지 대상은 계속.
    - **주제 확정 실패**(TopicSelectionError)는 콘텐츠 경로가 아예 없으므로 사이클 failed 종결.
    - **인프라/영속화 오류**(CycleStore save 실패 등 예상 밖 예외)는 격리하지 않는다 — 사이클을
      `running`으로 방치하지 않게 failed로 표기(best-effort)한 뒤 **호출자에게 전파**한다
      (무인 운영의 사이클 최상위 catch가 알림/재시도를 결정, FR-W4).
    - 전 대상이 실패(prepared=0)하면 사이클 status=failed(무인 운영에서 completed 오독 방지).
    """
    if not targets:
        raise ValueError("targets가 비어 있음 — 발행 대상 채널이 필요")

    cycle_id = store.create_cycle(goal_ref)
    store.log_event(
        cycle_id=cycle_id,
        kind="cycle_started",
        payload={"goal_ref": goal_ref, "target_count": len(targets)},
    )

    try:
        # 근접중복 판정의 재료 — 사이클당 1회만 읽는다(대상마다 다시 읽을 이유가 없다).
        recent_signatures = tuple(
            spec_signature(spec)
            for spec in store.recent_media_specs(days=RECENT_TOPIC_DAYS, limit=RECENT_SPEC_LIMIT)
        )

        # ── 주제(사이클당 1건, 통제변수: 동일 주제 도메인) ──────────────
        try:
            # 최근 발행 주제는 후보에서 뺀다. 트렌드 소스는 같은 항목을 며칠씩 노출해서,
            # 이게 없으면 어제와 같은 영상이 나간다(실제로 그랬다 — 2026-08-20/21 Cursor).
            topic = run_topic(
                model,
                platform=targets[0].platform,
                research_trends=research_trends,
                read_stats=read_stats,
                exclude_titles=store.recent_topic_titles(days=RECENT_TOPIC_DAYS),
                # 온보딩 채널 프로필 주입점(기본 None = 기존 동작 무변경).
                categories=topic_categories,
                guidance=channel_brief,
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

        # ── 대상별 콘텐츠 제작·적재(도메인 오류만 격리) ─────────────────
        results: list[TargetResult] = []
        for target in targets:
            # manual(수동) 대상: AI 초안·기계 발행 없이 같은 주제만 배정한다 —
            # 사람이 직접 작성·발행 후 sns.publish.manual로 등록한다(FR-E5).
            if target.mode == "manual":
                store.log_event(
                    cycle_id=cycle_id,
                    kind="notice",
                    payload={
                        "reason": "manual_assignment",
                        "channel_id": target.channel_id,
                        "topic_id": topic_id,
                        "topic_title": topic.title,
                    },
                )
                results.append(
                    TargetResult(channel_id=target.channel_id, outcome="manual_assigned")
                )
                continue
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
                        resolve_media_spec=resolve_media_spec,
                        recent_signatures=recent_signatures,
                        playbook_guidance=playbook_guidance,
                        topic_major=topic_major,
                        supported_methods=supported_methods,
                    )
                )
            except (ContentRejected, CardSpecError, VideoSpecError) as exc:
                store.log_event(
                    cycle_id=cycle_id,
                    kind="error",
                    payload={
                        "stage": "content",
                        "channel_id": target.channel_id,
                        "reason": str(exc),
                    },
                )
                results.append(
                    TargetResult(channel_id=target.channel_id, outcome="failed", error=str(exc))
                )

        # 전 대상 실패면 failed(무인 운영에서 completed 오독 방지).
        # manual 배정도 전진으로 친다 — 주제 전달이 그 대상의 이번 사이클 몫 전부다.
        prepared = sum(t.outcome == "prepared" for t in results)
        assigned = sum(t.outcome == "manual_assigned" for t in results)
        status: Literal["completed", "failed"] = "completed" if prepared or assigned else "failed"
        store.complete_cycle(cycle_id, status=status)
        store.log_event(
            cycle_id=cycle_id,
            kind="cycle_completed",
            payload={
                "prepared": prepared,
                "manual_assigned": assigned,
                "total": len(results),
                "status": status,
            },
        )
        return CycleResult(
            cycle_id=cycle_id, status=status, topic_id=topic_id, targets=tuple(results)
        )
    except Exception:
        # 인프라/영속화 등 예상 밖 실패 — running으로 방치하지 않고 failed 표기 후 전파.
        _mark_failed_best_effort(store, cycle_id)
        raise


def _mark_failed_best_effort(store: CycleStore, cycle_id: str) -> None:
    """원 예외를 삼키지 않도록, 종결 표기 중 2차 실패는 무시한다."""
    try:
        store.complete_cycle(cycle_id, status="failed")
        store.log_event(cycle_id=cycle_id, kind="error", payload={"stage": "cycle_infra"})
    except Exception:
        pass


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
    resolve_media_spec: ResolveMediaSpec | None,
    recent_signatures: tuple[frozenset[str], ...],
    playbook_guidance: str | None,
    topic_major: str,
    supported_methods: Sequence[VideoMethod],
) -> TargetResult:
    fmt = target.content_format

    content = run_content(
        model,
        topic=topic,
        content_format=fmt,
        playbook_guidance=playbook_guidance,
        topic_major=topic_major,
        supported_methods=supported_methods,
    )
    store.log_event(
        cycle_id=cycle_id,
        kind="agent_called",
        payload={"agent": "content", "channel_id": target.channel_id, "hook": content.hook_pattern},
    )

    # ── 발행 관문 (FR-Q7 안전 + FR-A2 근접중복) ────────────────────
    # **렌더 앞에 둔다.** 막힐 콘텐츠에 TTS·이미지 생성 비용을 쓸 이유가 없다.
    # 검사 대상은 캡션과 spec의 모든 텍스트다 — 자막·나레이션도 그대로 나가므로
    # 캡션만 보면 뚫린다. 이미지 해소는 텍스트를 바꾸지 않으므로 그 전에 봐도 같다.
    findings = screen_content(body=content.body, media_spec=content.media_spec)
    similarity = max_similarity(spec_signature(content.media_spec), recent_signatures)
    reasons = [f.describe() for f in findings]
    if similarity > MAX_CONTENT_SIMILARITY:
        reasons.append(
            f"직전 콘텐츠와 유사도 {similarity:.2f} (상한 {MAX_CONTENT_SIMILARITY}) — 근접중복"
        )
    if reasons:
        store.log_event(
            cycle_id=cycle_id,
            kind="notice",
            payload={"gate": "publish", "channel_id": target.channel_id, "reasons": reasons},
        )
        # 렌더하지 않는다. 사람이 볼 근거는 원고(body·media_spec)로 충분하다.
        return TargetResult(
            channel_id=target.channel_id,
            outcome="blocked",
            content_item_id=store.save_content_item(
                cycle_id=cycle_id,
                topic_id=topic_id,
                content_format=fmt,
                body=content.body,
                media_spec=content.media_spec,
                hook_pattern=content.hook_pattern,
                status="needs_review",
            ),
            error=" / ".join(reasons),
        )

    # 사진 해소는 **렌더 전에** 끝난다 — 렌더는 못박힌 바이트만 읽어야 결정론이 선다.
    # 크레딧 줄은 Pexels API 가이드라인 요구사항이라 캡션에 붙여 원장에 남긴다.
    media_spec: Mapping[str, object] = content.media_spec
    body = content.body
    if resolve_media_spec is not None:
        resolution = resolve_media_spec(media_spec)
        media_spec, body = resolution.media_spec, with_image_credits(body, resolution.media_spec)
        if resolution.notes:
            store.log_event(
                cycle_id=cycle_id,
                kind="notice",
                payload={"tool": "resolve_images", "notes": list(resolution.notes)},
            )
    # AI 생성 표기 — **해소 후 spec을 본다.** if 블록 밖에 두는 이유는 해소기가 없는
    # 배선에서도 표기가 붙어야 하기 때문이다. 빠지면 표기 없는 합성 영상이 나간다.
    body = with_ai_disclosure(body, media_spec)

    kind = _FORMAT_KIND[fmt]
    media = render_media(media_spec, kind)
    store.log_event(
        cycle_id=cycle_id,
        kind="tool_called",
        payload={"tool": "render_media", "kind": kind, "checksum": media.checksum},
    )

    if assess_quality is not None:
        report = assess_quality(media_spec=media_spec, media=media, content_format=fmt)
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
        body=body,
        media_spec=media_spec,
        hook_pattern=content.hook_pattern,
        status=DRAFT_STATUS[target.mode],
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
        content_item_id=content_item_id, channel_id=target.channel_id, mode=target.mode
    )
    return TargetResult(
        channel_id=target.channel_id,
        outcome="prepared",
        content_item_id=content_item_id,
        media_asset_id=media_asset_id,
        publication_id=publication_id,
    )
