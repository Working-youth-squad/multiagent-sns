"""시드 사이클 결과 → 대화에 실을 페이로드 (FR-W6 · FR-W5).

`role='system'` 메시지의 `payload` **모양을 여기 한 곳에서 정한다**. 만드는 쪽은 조립
루트(`scripts/run_chat_web.py`의 백그라운드 워커)이고 읽는 쪽은 화면
([sns.web.chat.render])이라, 계약이 두 파일에 흩어지면 조용히 어긋난다.

사이클이 *무엇을 만들었는지*까지 대화에 남기는 이유: "초안 3건을 만들었습니다"라는
문장만으로는 사용자가 무엇이 나올지 모른 채 승인 화면으로 건너가야 한다. 본문과 카드를
대화에서 바로 보여주고, 고칠 것이 있으면 승인 화면으로 보낸다.

**본문은 잘라서 싣는다.** 정본은 `content_item.body`고 승인 화면이 그걸 편집한다 —
대화에 실리는 것은 미리보기다. 잘렸다는 사실은 화면이 밝힌다.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

SEED_STARTED = "seed_started"
SEED_DONE = "seed_done"
SEED_NO_TARGET = "seed_no_target"
SEED_FAILED = "seed_failed"
SEED_CRASHED = "seed_crashed"
SEED_UNWIRED = "seed_unwired"

BODY_PREVIEW_CHARS = 400
"""대화에 싣는 본문 미리보기 상한. 정본은 content_item.body다."""


@dataclass(frozen=True)
class DraftItem:
    """사이클이 대상 1건에 대해 만든 것. 실패도 같은 자리에 담는다(사유가 곧 결과다)."""

    channel_label: str
    """사람이 읽는 채널 이름 — "instagram @handle"."""

    outcome: Literal["prepared", "blocked", "manual_assigned", "failed"]
    content_item_id: str | None = None
    body: str = ""
    media_asset_id: str | None = None

    media_kind: str | None = None
    """`media_asset.kind` — 화면이 <img>로 그릴지 <video>로 그릴지의 유일한 근거.

    자산 id만 실어 보내면 화면은 종류를 알 길이 없어 이미지를 가정하게 된다 — mp4가
    오면 깨진 이미지가 뜬다. 확장자로 추측하지 않는 이유는 저장소 URL이 화면에 오지
    않기 때문이다(`/media/{id}` 중계).
    """

    content_status: str | None = None
    """`content_item.status` — **발행을 실제로 막는 값**(hybrid면 needs_review).

    미디어 `quality_status`와 헷갈리기 쉽다: 카드 품질이 passed여도 사람 승인 전이면
    나가지 않는다. 화면의 주 뱃지는 이쪽이어야 한다.
    """

    quality_status: str | None = None
    """`media_asset.quality_status` — 렌더 산출물의 품질 판정. 승인 여부가 아니다."""

    error: str | None = None


@dataclass(frozen=True)
class SeedOutcome:
    """한 시드 사이클의 결과 전량."""

    cycle_id: str
    status: str
    topic_title: str
    items: Sequence[DraftItem] = field(default_factory=tuple)

    @property
    def prepared(self) -> tuple[DraftItem, ...]:
        return tuple(i for i in self.items if i.outcome == "prepared")


def seed_done_payload(outcome: SeedOutcome, *, approve_base: str) -> dict[str, object]:
    """`role='system'` 메시지에 실을 dict. 화면이 이 모양을 그대로 읽는다.

    `approve_base`는 승인 화면 주소(기본 http://127.0.0.1:8001). 링크를 페이로드에
    박아 두는 이유는 대화가 **나중에 다시 그려지기 때문**이다 — 그때 승인 서버가 어느
    주소였는지 화면이 알 길이 없다.
    """
    base = approve_base.rstrip("/")
    return {
        "kind": SEED_DONE,
        "cycle_id": outcome.cycle_id,
        "status": outcome.status,
        "topic_title": outcome.topic_title,
        "prepared_count": len(outcome.prepared),
        "items": [
            {
                "channel_label": item.channel_label,
                "outcome": item.outcome,
                "content_item_id": item.content_item_id,
                "body_preview": item.body[:BODY_PREVIEW_CHARS],
                # 잘렸다는 사실을 화면이 밝힐 수 있게 원본 길이를 같이 싣는다.
                "body_length": len(item.body),
                "media_asset_id": item.media_asset_id,
                "media_kind": item.media_kind,
                "content_status": item.content_status,
                "quality_status": item.quality_status,
                "error": item.error,
                "approve_url": (
                    f"{base}/items/{item.content_item_id}"
                    if item.content_item_id and item.outcome == "prepared"
                    else None
                ),
            }
            for item in outcome.items
        ],
    }


def seed_done_message(outcome: SeedOutcome) -> str:
    """페이로드와 함께 저장할 한 줄. 화면이 카드를 못 그리는 경우에도 사실은 남는다."""
    prepared = len(outcome.prepared)
    if prepared:
        return f"‘{outcome.topic_title}’ 초안 {prepared}건을 만들었습니다."
    if not outcome.items:
        return f"‘{outcome.topic_title}’ 초안을 만들지 못했습니다 — 대상이 없습니다."
    reasons = "; ".join(i.error or i.outcome for i in outcome.items)
    return f"‘{outcome.topic_title}’ 초안을 만들지 못했습니다 — {reasons}"


@dataclass(frozen=True)
class ExportItem:
    """수동 발행용 내보내기 1건 — 사람이 플랫폼 앱에 손으로 올릴 재료 전부.

    초안 카드가 싣는 미리보기와 달리 **본문을 자르지 않는다**. 손으로 올릴 때 잘린
    캡션을 복사하면 그대로 잘린 채 게시된다 — 미리보기와 정반대의 요구다.
    """

    content_item_id: str
    topic_title: str
    channel_label: str
    platform: str
    content_status: str
    body: str
    media_asset_id: str | None = None

    media_kind: str = "image"
    """`media_asset.kind`. 화면 표기와 내려받을 확장자가 **여기 하나에서** 갈린다."""

    channel_mode: str | None = None
    """채널 발행 모드 — 손으로 올린 뒤 **원장에 등록할 수 있는지**를 가른다.

    `sns.publish.manual`은 `manual` 채널만 받는다(auto/hybrid에 손으로 등록하면 실험군이
    오염된다). 그래서 hybrid 건을 손으로 올리면 등록 경로가 없고 `publication`은 pending
    으로 남는다 — 화면이 그 사실을 말해야 한다. 되는 것처럼 안내하면 실패로 보낸다.
    """

    @property
    def media_ext(self) -> str:
        """내려받을 파일 확장자. `media_kind`에서 파생한다 — 둘을 따로 실어 나르면
        한쪽만 갱신돼 mp4를 .png로 내려주는 날이 온다."""
        return "mp4" if self.media_kind == "video" else "png"

    @property
    def filename_stem(self) -> str:
        """내려받을 파일 이름의 몸통 — 사람이 여러 건을 받아도 구분되게.

        제목을 그대로 쓰면 OS가 거부하는 문자(`/` `:` 등)가 섞이므로 골라서 남긴다.
        """
        safe = "".join(c if c.isalnum() or c in " -_" else "" for c in self.topic_title).strip()
        safe = "-".join(safe.split()) or "content"
        return f"{safe}-{self.content_item_id[:8]}"
