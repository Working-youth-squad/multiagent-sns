"""수동(manual) 발행 등록 경계 (FR-E5) — "수동 발행 + ID 등록".

manual 모드는 사람이 사이클이 배정한 주제(같은 프롬프트)를 받아 직접 작성하고
플랫폼 앱에서 직접 발행한다. 시스템의 몫은 발행이 아니라 **등록**이다: 발행된
게시물의 `external_post_id`를 받아 `content_item`(사람 작성 본문) +
`publication`(status=published, mode=manual)을 만들어, 이후 지표 수집·비교
리포트가 기계 발행 건과 동일한 경로로 굴러가게 한다.

증빙 규율:
- `publication.mode='manual'` 스냅샷이 비교의 단일 출처([sns.publish.modes]).
- **manual 채널에만 등록 가능** — auto/hybrid 채널에 손으로 등록해 실험군이
  오염되는 것을 차단한다.
- 멱등: 같은 (채널, external_post_id) 재등록은 기존 행을 돌려준다. 물리 강제는
  002의 부분 유니크 인덱스.

커넥션은 **autocommit**을 가정한다([sns.publish.stores] docstring과 동일 규율).
"""

from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg import errors
from psycopg.types.json import Json

from sns.tools.contracts import ContentFormat


class ManualRegistrationError(ValueError):
    """manual 등록 불가 — 채널 없음/모드 불일치/필수값 결손."""


@dataclass(frozen=True)
class ManualRegistration:
    publication_id: str
    content_item_id: str
    already_registered: bool  # 멱등 재등록이면 True(신규 행 없음)


def register_manual_publication(
    conn: psycopg.Connection,
    *,
    channel_id: str,
    cycle_id: str,
    topic_id: str,
    content_format: ContentFormat,
    body: str,
    external_post_id: str,
    published_at: datetime | None = None,
) -> ManualRegistration:
    """사람이 직접 발행한 게시물 1건을 원장에 등록한다(멱등).

    `cycle_id`·`topic_id`는 그 발행이 어느 사이클의 어느 주제(프롬프트)에 대한
    수동 산출인지 묶는다 — 같은 프롬프트 3모드 비교의 전제. `published_at`이
    None이면 등록 시각을 쓴다(실제 발행 시각을 알면 넘겨라).
    """
    if not external_post_id:
        raise ManualRegistrationError("external_post_id가 비어 있음")

    row = conn.execute("SELECT mode FROM channel WHERE id = %s", (channel_id,)).fetchone()
    if row is None:
        raise ManualRegistrationError(f"채널 없음: {channel_id}")
    if row[0] != "manual":
        # 기계 발행 채널에 수동 등록 = 실험군 오염 → 거부.
        raise ManualRegistrationError(f"manual 채널이 아님(mode={row[0]}): {channel_id}")

    existing = _find_existing(conn, channel_id, external_post_id)
    if existing is not None:
        return existing

    try:
        with conn.transaction():
            ci_row = conn.execute(
                # 사람이 처음부터 끝까지 쓴 본문 — 승인 관문이 따로 없고,
                # edited_by_human은 '사람 손을 탄 콘텐츠' 축(FR-E3)으로 true.
                "INSERT INTO content_item "
                "(cycle_id, topic_id, format, status, body, edited_by_human) "
                "VALUES (%s, %s, %s, 'approved', %s, true) RETURNING id",
                (cycle_id, topic_id, content_format, body),
            ).fetchone()
            assert ci_row is not None
            pub_row = conn.execute(
                "INSERT INTO publication "
                "(content_item_id, channel_id, status, mode, external_post_id, published_at) "
                "VALUES (%s, %s, 'published', 'manual', %s, COALESCE(%s, now())) RETURNING id",
                (ci_row[0], channel_id, external_post_id, published_at),
            ).fetchone()
            assert pub_row is not None
            conn.execute(
                "INSERT INTO run_event (cycle_id, kind, payload) VALUES (%s, %s, %s)",
                (
                    cycle_id,
                    "publish_attempted",
                    Json(
                        {
                            "publication_id": str(pub_row[0]),
                            "mode": "manual",
                            "state": "published",
                            "external_post_id": external_post_id,
                            "registered_manual": True,
                        }
                    ),
                ),
            )
    except errors.UniqueViolation:
        # 동시 등록 경합 — 유니크 인덱스가 막았으니 승자 행을 돌려준다.
        raced = _find_existing(conn, channel_id, external_post_id)
        assert raced is not None
        return raced

    return ManualRegistration(
        publication_id=str(pub_row[0]),
        content_item_id=str(ci_row[0]),
        already_registered=False,
    )


def _find_existing(
    conn: psycopg.Connection, channel_id: str, external_post_id: str
) -> ManualRegistration | None:
    row = conn.execute(
        "SELECT id, content_item_id FROM publication "
        "WHERE channel_id = %s AND external_post_id = %s",
        (channel_id, external_post_id),
    ).fetchone()
    if row is None:
        return None
    return ManualRegistration(
        publication_id=str(row[0]), content_item_id=str(row[1]), already_registered=True
    )
