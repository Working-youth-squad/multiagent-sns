"""수동(manual) 발행 등록 CLI (FR-E5).

사람이 플랫폼 앱에서 **이미 직접 발행한** 게시물을 원장에 등록한다. 이 스크립트는
발행을 대신하지 않는다 — external_post_id를 손으로 받아와 넘기면 등록만 된다
([sns.publish.manual] 참조). 채널이 manual 모드가 아니거나 (채널, post id)가 이미
등록돼 있으면 오류/멱등 처리는 그쪽 모듈의 규율을 그대로 따른다.

실행 예:
    uv run python scripts/manual_register.py \
        --channel-id <uuid> --cycle-id <uuid> --topic-id <uuid> \
        --format feed_image --external-post-id 17912345678 \
        --body-file draft.txt

전제: docker compose up -d postgres. 등록 완료 알림은 DISCORD_WEBHOOK_URL이 있으면
자동 발송되고, 없으면 DB 적재만 된다(FR-W4 규율과 동일). --no-notify로 완전히 끈다.
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from sns.notify.alerts import manual_registered
from sns.notify.discord import discord_sender_from_env
from sns.notify.dispatch import PgAlertSink, dispatch_alert
from sns.publish.manual import ManualRegistrationError, register_manual_publication

ENV_FILE = Path(__file__).parent.parent / ".env"
DEFAULT_DSN = "postgresql://sns:sns@localhost:5432/sns"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--channel-id", required=True, help="manual 모드 channel.id")
    parser.add_argument("--cycle-id", required=True, help="주제를 배정한 cycle.id")
    parser.add_argument("--topic-id", required=True, help="배정된 topic.id")
    parser.add_argument("--format", required=True, choices=["feed_image", "reels", "shorts"])
    parser.add_argument("--external-post-id", required=True, help="플랫폼이 발급한 게시물 id")
    parser.add_argument("--body", default=None, help="본문 텍스트(직접 입력)")
    parser.add_argument("--body-file", default=None, help="본문 텍스트 파일 경로")
    parser.add_argument("--published-at", default=None, help="ISO8601. 생략 시 등록 시각을 쓴다")
    parser.add_argument("--no-notify", action="store_true", help="등록 완료 알림 발송 생략")
    return parser


def _read_body(args: argparse.Namespace) -> str:
    if args.body_file:
        return Path(args.body_file).read_text(encoding="utf-8")
    if args.body is not None:
        return args.body
    raise SystemExit("중단: --body 또는 --body-file 중 하나가 필요합니다.")


def _channel_platform(conn: psycopg.Connection, channel_id: str) -> str:
    row = conn.execute("SELECT platform FROM channel WHERE id = %s", (channel_id,)).fetchone()
    if row is None:
        raise SystemExit(f"중단: 채널 없음: {channel_id}")
    return str(row[0])


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    load_dotenv(ENV_FILE, override=False)
    args = build_parser().parse_args(argv)
    body = _read_body(args)
    published_at = datetime.fromisoformat(args.published_at) if args.published_at else None

    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    try:
        conn = psycopg.connect(dsn, connect_timeout=10, autocommit=True)
    except psycopg.OperationalError as exc:
        print(f"중단: PostgreSQL 연결 실패 — docker compose up -d postgres\n      {exc}")
        return 1

    platform = _channel_platform(conn, args.channel_id)

    try:
        result = register_manual_publication(
            conn,
            channel_id=args.channel_id,
            cycle_id=args.cycle_id,
            topic_id=args.topic_id,
            content_format=args.format,
            body=body,
            external_post_id=args.external_post_id,
            published_at=published_at,
        )
    except ManualRegistrationError as exc:
        print(f"등록 실패: {exc}")
        return 1

    action = "이미 등록됨(멱등)" if result.already_registered else "신규 등록"
    print(
        f"{action}: publication_id={result.publication_id} content_item_id={result.content_item_id}"
    )

    if not args.no_notify and not result.already_registered:
        alert = manual_registered(
            platform,
            channel_id=args.channel_id,
            external_post_id=args.external_post_id,
            publication_id=result.publication_id,
        )
        sender = discord_sender_from_env()
        if sender is None:
            print("DISCORD_WEBHOOK_URL 없음 — DB 적재만(전송 생략)")
        outcome = dispatch_alert(alert, sink=PgAlertSink(conn), sender=sender)
        print(f"알림: DB적재={outcome.recorded} Discord전송={outcome.delivered}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
