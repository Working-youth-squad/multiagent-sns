"""발행 모드 3분류 정본 (FR-E1·E5) — 수동(manual) · 반자동(hybrid) · 자동(auto).

실험 질문: **같은 프롬프트(주제)를 넣었을 때 어느 운영 방식이 가장 효과적인가.**
세 모드는 "사람이 어디까지 개입하는가"로만 갈리고, 나머지(주제·발행 슬롯·지표
수집)는 통제한다:

- ``auto``   (자동)  : AI 생성 → 품질 게이트 → 기계 발행. 사람 개입 0.
- ``hybrid`` (반자동): AI 초안 → 사람 검수·수정·승인(needs_review 관문) → 기계 발행.
- ``manual`` (수동)  : 사람이 같은 주제를 받아 직접 작성·플랫폼에서 직접 발행 →
  ``external_post_id``만 시스템에 등록([sns.publish.manual]). 기계 발행 경로에
  절대 들어가지 않는다.

증빙(어느 모드의 발행인지)은 ``publication.mode`` 스냅샷이 단일 출처다 —
``channel.mode``는 바뀔 수 있으므로 발행 행에 발행 시점 모드를 굳힌다.
보조 증빙: ``content_item.edited_by_human``(hybrid 수정 여부), append-only
``run_event``(publish_attempted payload의 mode).

``channel.mode``에는 실험 모드 외에 운영 상태값 ``off``(발행 중지)가 더 있다 —
비교 대상이 아니므로 이 분류에 포함하지 않는다.
"""

from typing import Literal

PublishMode = Literal["auto", "hybrid", "manual"]

# 기계(발행 러너)가 발행하는 모드. manual은 사람이 직접 발행하므로 제외 —
# publish/runner의 대기 건 선택과 반드시 일치해야 한다.
MACHINE_MODES: frozenset[PublishMode] = frozenset({"auto", "hybrid"})

# 사이클에서 AI 초안이 받는 초기 content_item.status (manual은 AI 초안 자체가 없다).
DRAFT_STATUS: dict[PublishMode, str] = {"auto": "approved", "hybrid": "needs_review"}


def is_machine_published(mode: PublishMode) -> bool:
    """이 모드의 발행을 기계가 수행하는가 (manual=사람이 직접 발행)."""
    return mode in MACHINE_MODES
