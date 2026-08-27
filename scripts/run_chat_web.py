"""키워드 챗봇 웹 서버 기동. uv run python scripts/run_chat_web.py

전제:
  1. docker compose up -d postgres (+ python -m sns.db.migrate — 마이그 004 필요)
  2. LLM 키 — **필수**(앱 본체가 대화다). env SNS_MODEL_PROVIDER로 고른다:
     · gemini(기본) → GEMINI_API_KEY   · openai → OPENAI_API_KEY
     모델은 GEMINI_MODEL / OPENAI_MODEL로 덮어쓴다.
  3. env DATABASE_URL — (선택) 기본 postgresql://sns:sns@localhost:5432/sns
  4. env CHAT_WEB_HOST/PORT — (선택) 기본 127.0.0.1:8003
  5. env CARD_FONT — (선택) 한글 TTF 경로. 미지정 시 자동 탐색
  6. env APPROVE_WEB_BASE — (선택) 승인 화면 주소. 기본 http://127.0.0.1:8001

영상(쇼츠·릴스) 관련 — **없으면 대화에 영상이 아예 안 뜬다**:
  7. ffmpeg/ffprobe + 나레이션 자격증명(GOOGLE_TTS_API_KEY 또는 ADC)
  8. env CHAT_FFMPEG — (선택) ffmpeg 경로. 기본 PATH의 `ffmpeg`
  9. env CHAT_VIDEO_STYLE — (선택) 화면 문법 3col|motion|clip. 기본 motion
 10. env CHAT_VIDEO_METHODS — (선택) 대화에 띄울 제작 방식. 기본 template.
     `template,generated_scene`처럼 **명시해야** 유료 방식이 켜진다(컷마다 이미지 생성)
 11. env CHAT_PUBLISH — (선택) `youtube`면 승인된 건을 실제로 올린다. 기본은 안 올림.
     ⚠️ 유튜브는 **API 키로 안 된다** — `.secrets/client_secret.json`(OAuth 데스크톱
     클라이언트)과 첫 실행 브라우저 동의가 필요하다. 첫 실전은 private 고정.

단일 커넥션 전제(scripts/run_approve_web.py와 동일 규율).

**시드 발행은 hybrid 채널에서만 돈다**(FR-W5). 확정된 주제는 초안·렌더·게이트를 거쳐
승인 대기로 들어가고, 사람이 승인 화면(scripts/run_approve_web.py, :8001)에서 확인한 뒤
발행된다 — 챗봇이 곧바로 세상에 올리지 않는다. `CHAT_PUBLISH`를 켜도 이 관문은 그대로다:
발행 러너는 hybrid 건을 `content_item.status='approved'` 전에는 집지 않는다(FR-Q3).

**포맷·제작 방식은 사용자가 대화에서 고른다.** 대화에 뜨는 목록은 이 서버가 실제로
배선한 것뿐이다(Capability Gate를 대화까지 — [sns.chat.agent]). 렌더 배선 자체는
[sns.runner.wiring]이 정본이고 프로필 CLI(run_profile_cycle.py)와 같은 함수를 쓴다.

**topic_major는 채널 프로필에서 온다.** 없으면 만들지 않고 온보딩으로 안내한다 —
조용한 개발 기본값으로 떨어지면 요리 채널에 코드 컷이 들어간다([sns.topic_policy]).

사이클은 **별도 스레드**에서 돈다. 폼 POST 전체 새로고침 방식이라 동기로 완주시키면
브라우저가 분 단위로 멈춘다. 진행·결과는 대화에 system 메시지로 붙으므로 사용자는
새로고침으로 확인한다.
"""

import os
import shutil
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import psycopg
import uvicorn
from dotenv import load_dotenv

from sns.adapters.youtube.auth import build_youtube, load_credentials
from sns.adapters.youtube.publisher import YouTubePublish
from sns.agents.models import make_model, required_key_env, resolve_model_name, resolve_provider
from sns.chat.agent import SeedRequest
from sns.chat.drafts import (
    DraftItem,
    ExportItem,
    SeedOutcome,
    seed_done_message,
    seed_done_payload,
)
from sns.chat.store import PgChatStore
from sns.onboarding.profile import ChannelProfile, build_channel_brief
from sns.onboarding.store import PgOnboardingStore
from sns.publish.router import PlatformPublishRouter
from sns.publish.runner import run_pending_publications
from sns.render.video.spec import DEFAULT_VOICE
from sns.render.video.tts import Synthesize, synthesize_google
from sns.research.trends import default_service
from sns.runner.cycle import CycleTarget, TargetResult, run_cycle
from sns.runner.formats import FormatChoice, content_format_for, parse_platform
from sns.runner.store import PgCycleStore
from sns.runner.wiring import VIDEO_STYLES, build_render_wiring, style_guidance
from sns.tools.contracts import MediaKind, Platform, Publish, VideoMethod
from sns.tools.fakes import FakeReadStats
from collections.abc import Callable

from sns.web.chat.app import LoadExportFn, LoadMediaFn, StartCycleFn, create_app
from sns.web.chat.render import ChatChannel

ENV_FILE = Path(__file__).parent.parent / ".env"
DEFAULT_DSN = "postgresql://sns:sns@localhost:5432/sns"
DEFAULT_APPROVE_BASE = "http://127.0.0.1:8001"
SECRETS = Path(__file__).parent.parent / ".secrets"
_MEDIA_TYPES = {"image": "image/png", "video": "video/mp4"}
OUT = Path(__file__).parent / "out"
FONT_CANDIDATES = (
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
)


class DirMediaStore:
    """렌더 바이트를 디스크에 (scripts/e2e_cycle.py와 동형)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, *, checksum: str, kind: MediaKind, ext: str) -> str:
        path = self.root / f"{kind}-{checksum[:16]}.{ext}"
        path.write_bytes(data)
        return path.resolve().as_uri()

    def get(self, url: str) -> bytes:
        from urllib.parse import urlparse
        from urllib.request import url2pathname

        return Path(url2pathname(urlparse(url).path)).read_bytes()


def find_font() -> str | None:
    env = os.environ.get("CARD_FONT")
    if env:
        return env
    return next((c for c in FONT_CANDIDATES if Path(c).exists()), None)


class SeedRefused(RuntimeError):
    """대화에 **그대로 보여줄** 거부 사유.

    스택트레이스가 아니라 사람이 읽을 문장이다 — 사용자는 주제를 확정한 뒤 결과를
    기다리는 중이고, 안 되는 이유와 무엇을 하면 되는지를 알아야 한다.
    """


@dataclass(frozen=True)
class SeedPlan:
    """이번 시드 사이클이 무엇을 어떤 프로필로 태울지 — 배선 전에 확정한다."""

    targets: tuple[CycleTarget, ...]
    profile: ChannelProfile
    """`topic_major`·`goal_ref`·캐릭터의 출처. **사이클 하나에 프로필 하나다** —
    `run_cycle`이 `topic_major`를 대상별이 아니라 사이클별로 받기 때문이다."""

    skipped: tuple[str, ...] = ()
    """이번 사이클에서 빠진 채널과 그 사유. 조용히 빠지면 사용자는 왜 초안이 한 건만
    나왔는지 알 수 없다."""


@dataclass(frozen=True)
class _Candidate:
    """시드 후보 채널 1건 — 검증을 통과한 값만 담는다."""

    channel_id: str
    platform: Platform
    label: str
    profile: ChannelProfile


def plan_seed(
    conn: psycopg.Connection, *, choice: FormatChoice, channel_id: str | None = None
) -> SeedPlan:
    """hybrid 채널 + 프로필 → 이번 사이클 계획. 못 돌면 `SeedRefused`.

    시드 발행 대상 = hybrid 채널 전부(FR-W5: 수동 시드는 hybrid에서만).
    `channel_id`가 오면 **그 채널 하나만** — 채널에 묶인 대화의 시드다.

    **프로필이 없는 채널은 태우지 않는다.** `topic_major`가 없으면 콘텐츠·렌더가 어떤
    주제 정책으로 돌지 정할 수 없다 — 예전엔 개발 기본값으로 조용히 떨어져 요리 채널에
    코드 컷이 들어갔고, 그래서 그 기본값을 없앴다([sns.topic_policy]). 웹에서도 같은
    규율을 지키되, 막다른 길로 두지 않고 온보딩으로 안내한다.
    """
    if channel_id:
        rows = conn.execute(
            "SELECT id, platform, handle FROM channel "
            "WHERE mode = 'hybrid' AND status = 'active' AND id = %s",
            (channel_id,),
        ).fetchall()
        if not rows:
            raise SeedRefused(
                "이 대화가 묶인 채널이 없거나 hybrid 모드가 아닙니다 — "
                "채널 화면에서 상태를 확인해주세요."
            )
    else:
        rows = conn.execute(
            "SELECT id, platform, handle FROM channel "
            "WHERE mode = 'hybrid' AND status = 'active' ORDER BY created_at"
        ).fetchall()
    if not rows:
        raise SeedRefused(
            "초안을 만들 채널이 없습니다 — hybrid 모드 채널이 필요합니다"
            " (온보딩 :8002에서 만들 수 있습니다)."
        )

    onboarding = PgOnboardingStore(conn)
    found: list[_Candidate] = []
    skipped: list[str] = []
    for channel_id, platform_raw, handle in rows:
        label = f"{platform_raw} @{handle}"
        platform = parse_platform(str(platform_raw))
        if platform is None:
            skipped.append(f"{label}(모르는 플랫폼)")
            continue
        profile = onboarding.latest_profile(str(channel_id))
        if profile is None:
            skipped.append(f"{label}(프로필 없음)")
            continue
        found.append(_Candidate(str(channel_id), platform, label, profile))

    if not found:
        raise SeedRefused(
            "hybrid 채널은 있지만 온보딩 프로필이 없어 초안을 만들 수 없습니다"
            f" — {', '.join(skipped)}."
            " 온보딩 :8002에서 인터뷰를 완료하면 그 채널로 만들 수 있습니다."
        )

    # 사이클 하나에 topic_major 하나다(run_cycle이 대상별이 아니라 사이클별로 받는다).
    # 프로필이 갈리는 채널은 이번 사이클에서 빼고 사실을 남긴다 — 남의 주제 정책으로
    # 렌더하는 것보다 안 만드는 편이 낫다.
    major = found[0].profile.topic_major
    same = [c for c in found if c.profile.topic_major == major]
    skipped += [
        f"{c.label}(주제 대분류 다름: {c.profile.topic_major})"
        for c in found
        if c.profile.topic_major != major
    ]

    targets = tuple(
        CycleTarget(
            channel_id=c.channel_id,
            platform=c.platform,
            content_format=content_format_for(c.platform, choice),
            mode="hybrid",
        )
        for c in same
    )
    return SeedPlan(targets=targets, profile=found[0].profile, skipped=tuple(skipped))


def _draft_item(conn: psycopg.Connection, target: TargetResult) -> DraftItem:
    """TargetResult + 원장 조회 → 대화에 실을 1건. 조회 실패가 결과 보고를 막지 않는다."""
    row = conn.execute(
        "SELECT platform, handle FROM channel WHERE id = %s", (target.channel_id,)
    ).fetchone()
    label = f"{row[0]} @{row[1]}" if row else target.channel_id[:8]

    body = ""
    content_status = None
    if target.content_item_id:
        found = conn.execute(
            "SELECT body, status FROM content_item WHERE id = %s", (target.content_item_id,)
        ).fetchone()
        if found:
            body = str(found[0]) if found[0] else ""
            content_status = str(found[1])

    quality = None
    media_kind = None
    if target.media_asset_id:
        found = conn.execute(
            "SELECT quality_status, kind FROM media_asset WHERE id = %s",
            (target.media_asset_id,),
        ).fetchone()
        if found:
            quality = str(found[0])
            media_kind = str(found[1])

    return DraftItem(
        channel_label=label,
        outcome=target.outcome,
        content_item_id=target.content_item_id,
        body=body,
        media_asset_id=target.media_asset_id,
        media_kind=media_kind,
        content_status=content_status,
        quality_status=quality,
        error=target.error,
    )


def make_load_media_fn(dsn: str) -> LoadMediaFn:
    """media_asset_id → (바이트, MIME). id로만 받아 임의 경로 읽기를 막는다."""
    store = DirMediaStore(OUT)

    def load(asset_id: str) -> tuple[bytes, str] | None:
        with psycopg.connect(dsn, connect_timeout=10, autocommit=True) as conn:
            row = conn.execute(
                "SELECT storage_url, kind FROM media_asset WHERE id = %s", (asset_id,)
            ).fetchone()
        if row is None:
            return None
        return store.get(str(row[0])), _MEDIA_TYPES.get(str(row[1]), "application/octet-stream")

    return load


def make_load_export_fn(dsn: str) -> LoadExportFn:
    """content_item_id → 수동 발행용 재료. **본문은 지금 원장의 값**을 읽는다.

    대화 payload의 미리보기가 아니라 여기서 다시 읽는 이유: 승인 화면에서 사람이 고친
    본문이 payload에는 없다. 손으로 올릴 캡션은 최신이어야 한다.
    """

    def load(content_item_id: str) -> ExportItem | None:
        with psycopg.connect(dsn, connect_timeout=10, autocommit=True) as conn:
            row = conn.execute(
                "SELECT ci.body, ci.status, ma.id, ma.kind, "
                "ch.platform, ch.handle, t.title, ch.mode "
                "FROM content_item ci "
                "JOIN topic t ON t.id = ci.topic_id "
                "LEFT JOIN media_asset ma ON ma.content_item_id = ci.id "
                "LEFT JOIN publication p ON p.content_item_id = ci.id "
                "LEFT JOIN channel ch ON ch.id = p.channel_id "
                "WHERE ci.id = %s",
                (content_item_id,),
            ).fetchone()
        if row is None:
            return None
        platform = str(row[4]) if row[4] else "플랫폼 미상"
        handle = f" @{row[5]}" if row[5] else ""
        return ExportItem(
            content_item_id=content_item_id,
            topic_title=str(row[6] or "제목 없음"),
            channel_label=f"{platform}{handle}",
            platform=platform,
            content_status=str(row[1]),
            body=str(row[0] or ""),
            media_asset_id=None if row[2] is None else str(row[2]),
            media_kind=str(row[3]) if row[3] else "image",
            channel_mode=None if row[7] is None else str(row[7]),
        )

    return load


def make_channels_fn(conn: psycopg.Connection) -> "Callable[[], tuple[ChatChannel, ...]]":
    """채널 선택 UI 재료 — hybrid + 프로필 있는 채널만(시드 가능한 것만 보여준다).

    주제 제안은 프로필의 세부 주제 + 추천안의 우선 세부 주제다. 대화 시작 전에
    "이 채널에서 뭘 다루면 좋을지"를 프로필이 이미 알고 있으므로 그걸 꺼내 보여준다.
    """
    onboarding = PgOnboardingStore(conn)

    def channels() -> tuple[ChatChannel, ...]:
        out: list[ChatChannel] = []
        for ch in onboarding.list_channels():
            if ch.mode != "hybrid":
                continue
            profile = onboarding.latest_profile(ch.channel_id)
            if profile is None:
                continue
            raw: list[str] = list(profile.topic_subs)
            rec = profile.recommendation or {}
            focus = rec.get("focus_subs")
            if isinstance(focus, list):
                raw += [str(s) for s in focus if str(s).strip()]
            seen: set[str] = set()
            suggestions = tuple(
                s for s in raw if not (s in seen or seen.add(s))
            )[:6]
            out.append(
                ChatChannel(
                    channel_id=ch.channel_id, label=ch.handle, suggestions=suggestions
                )
            )
        return tuple(out)

    return channels


def playbook_guidance(profile: ChannelProfile, request: SeedRequest, style: str) -> str:
    """Content 에이전트에게 줄 지침 — 채널 브리프 + (영상이면) 화면 문법.

    프로필 CLI와 같은 조합이다. 챗봇만 화면 문법을 빼면 같은 style이 진입점에 따라
    다른 대본을 낳는다(모션 화면에서 코드 컷이 그라데이션으로 강등된다).
    """
    brief = build_channel_brief(profile)
    guidance = style_guidance(style) if request.content_format == "video" else ""
    return f"{brief}\n{guidance}" if guidance else brief


def make_start_cycle_fn(
    dsn: str,
    chat_store: PgChatStore,
    *,
    approve_base: str,
    style: str,
    ffmpeg: str,
    publish: Publish | None,
) -> StartCycleFn:
    """(conversation_id, SeedRequest) → 백그라운드 사이클. 즉시 반환한다.

    `publish`가 있으면 사이클이 끝난 뒤 발행 러너를 한 번 돌린다. **hybrid 채널은
    사람 승인 전에는 발행 진입하지 않으므로**([sns.publish.runner]의 content_status
    게이트) 이 호출은 승인 전엔 `awaiting_review`로 끝난다 — 챗봇이 곧바로 세상에
    올리는 경로가 아니다. None이면 승인 대기까지만 만든다(기본).
    """
    font = find_font()

    def worker(conversation_id: str, request: SeedRequest, channel_id: str | None) -> None:
        # 스레드마다 자기 커넥션을 연다 — psycopg 커넥션은 스레드 간 공유 대상이 아니고,
        # 웹 요청이 쓰는 커넥션을 분 단위 작업이 붙들면 화면이 멈춘다.
        topic = request.topic
        try:
            with psycopg.connect(dsn, connect_timeout=10, autocommit=True) as conn:
                try:
                    plan = plan_seed(
                        conn, choice=request.content_format, channel_id=channel_id
                    )
                except SeedRefused as refused:
                    chat_store.append(
                        conversation_id,
                        role="system",
                        body=str(refused),
                        payload={"kind": "seed_no_target", "reason": str(refused)},
                    )
                    return

                profile = plan.profile
                # 배선 정본은 [sns.runner.wiring] — 프로필 CLI와 같은 함수를 부른다.
                # 사이클 하나에 배선 하나다(생성 예산이 배선에 딸려 있다).
                wiring = build_render_wiring(
                    kind=request.content_format,
                    store=DirMediaStore(OUT),
                    topic_major=profile.topic_major,
                    font=font,
                    style=style,
                    methods=(request.method,) if request.method else ("template",),
                    character_image_url=profile.character_image_url,
                    character_style=profile.character_style,
                    ffmpeg=ffmpeg,
                )
                result = run_cycle(
                    PgCycleStore(conn),
                    goal_ref=profile.goal_ref,
                    topic_major=profile.topic_major,
                    targets=plan.targets,
                    model=make_model(),
                    # seed_topic이 있으면 주제 선택 경로를 타지 않아 이 둘은 호출되지
                    # 않는다. 계약상 필수라 넘길 뿐이다(run_cycle docstring).
                    research_trends=default_service(),
                    read_stats=FakeReadStats(),
                    render_media=wiring.render_media,
                    assess_quality=wiring.assess_quality,
                    resolve_media_spec=wiring.resolve_media_spec,
                    supported_methods=wiring.supported_methods,
                    channel_brief=build_channel_brief(profile),
                    topic_categories=profile.categories,
                    playbook_guidance=playbook_guidance(profile, request, style),
                    seed_topic=topic,
                )
                outcome = SeedOutcome(
                    cycle_id=result.cycle_id,
                    status=result.status,
                    topic_title=topic.title,
                    items=tuple(_draft_item(conn, t) for t in result.targets),
                )
                body = seed_done_message(outcome)
                if plan.skipped:
                    # 조용히 빠지면 사용자는 왜 초안이 덜 나왔는지 알 수 없다.
                    body += f" (제외된 채널: {', '.join(plan.skipped)})"
                chat_store.append(
                    conversation_id,
                    role="system",
                    body=body,
                    payload=seed_done_payload(outcome, approve_base=approve_base),
                )
                if publish is not None:
                    _run_publish(conn, conversation_id, chat_store, publish)
        except Exception as exc:  # 스레드에서 죽으면 사용자는 영영 모른다 — 대화에 남긴다
            chat_store.append(
                conversation_id,
                role="system",
                body=f"초안 제작이 실패했습니다: {exc}",
                payload={"kind": "seed_crashed", "error": str(exc)},
            )

    def start(conversation_id: str, request: SeedRequest, channel_id: str | None) -> None:
        threading.Thread(
            target=worker, args=(conversation_id, request, channel_id), daemon=True
        ).start()

    return start


def _run_publish(
    conn: psycopg.Connection, conversation_id: str, chat_store: PgChatStore, publish: Publish
) -> None:
    """발행 러너 1회. **실패가 초안 결과 보고를 되돌리지 않는다** — 초안은 이미 만들어졌다."""
    try:
        results = run_pending_publications(conn, publish)
    except Exception as exc:
        chat_store.append(
            conversation_id,
            role="system",
            body=f"발행 러너가 실패했습니다: {exc} (초안은 그대로 남아 있습니다)",
            payload={"kind": "publish_failed", "error": str(exc)},
        )
        return
    if not results:
        return
    counts: dict[str, int] = {}
    for r in results:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
    # awaiting_review가 정상이다 — hybrid는 사람 승인 전에 발행 진입하지 않는다(FR-Q3).
    summary = ", ".join(f"{k} {v}건" for k, v in sorted(counts.items()))
    chat_store.append(
        conversation_id,
        role="system",
        body=f"발행 러너 결과: {summary}.",
        payload={
            "kind": "publish_ran",
            "counts": counts,
            "results": [
                {"publication_id": r.publication_id, "outcome": r.outcome} for r in results
            ],
        },
    )


def tts_missing(synthesize: Synthesize | None = None) -> str | None:
    """나레이션이 **실제로 되는지** 한 번 합성해 본다. 안 되면 그 사유, 되면 None.

    자격증명 *존재*만 보면 부족하다. env 키가 없어도 ADC가 1급 경로라 그쪽도 봐야
    하는데(조직 정책이 API 키 발급을 막는 계정에서는 ADC가 유일하다), `google.auth`가
    자격을 찾아준다고 그 자격에 TTS 권한이 있다는 뜻은 아니다 — 실제로 이 개발
    환경에서 ADC는 잡히는데 프로젝트에 권한이 없어 렌더 직전 403이 났다. 사용자는
    영상을 고르고 몇 분을 기다린 끝에 그 403을 만난다.

    그래서 **기동 시점에 두 글자를 합성해 본다.** 서버당 한 번이고 무료 한도(월 100만
    자) 대비 무시할 수 있는 크기다 — 그 대가로 대화 목록이 거짓말을 하지 않는다.
    """
    # 기본값을 인자에 묶지 않는다 — 기본 인자는 def 시점에 값이 고정돼 배선을
    # 갈아 끼울 수 없다(그래서 테스트가 실 TTS를 때렸다).
    fn = synthesize or synthesize_google
    try:
        fn("확인", voice=DEFAULT_VOICE)
    except Exception as exc:
        reason = str(exc).splitlines()[0][:160]
        return f"나레이션(TTS) 사용 불가 — {reason}"
    return None


def wired_formats(
    ffmpeg: str, *, synthesize: Synthesize | None = None
) -> tuple[tuple[FormatChoice, ...], list[str]]:
    """이 환경이 실제로 만들 수 있는 포맷 — 대화에 뜨는 목록의 출처.

    **없는 것을 대화에 띄우지 않는다**(Capability Gate를 대화까지). 영상은 ffmpeg과
    나레이션 자격증명 둘 다 있어야 한다 — 하나라도 없으면 사용자가 영상을 고른 뒤
    분 단위를 기다린 끝에 실패를 만난다.
    """
    missing: list[str] = []
    if shutil.which(ffmpeg) is None and not Path(ffmpeg).exists():
        missing.append(f"ffmpeg({ffmpeg})")
    tts = tts_missing(synthesize)
    if tts:
        missing.append(tts)
    return (("card",) if missing else ("card", "video")), missing


def wired_methods() -> tuple[VideoMethod, ...]:
    """대화에 띄울 영상 제작 방식. `generated_scene`은 **컷마다 유료 이미지**라
    env로 명시해야 들어온다 — 기본값에 두면 결제가 켜진 계정에서 조용히 돈을 쓴다.

        CHAT_VIDEO_METHODS=template,generated_scene
    """
    raw = os.environ.get("CHAT_VIDEO_METHODS", "template")
    allowed: dict[str, VideoMethod] = {
        "template": "template",
        "generated_scene": "generated_scene",
    }
    picked = [allowed[n] for n in (v.strip() for v in raw.split(",")) if n in allowed]
    return tuple(dict.fromkeys(picked)) or ("template",)


def make_publish() -> Publish | None:
    """`CHAT_PUBLISH=youtube`면 실 업로드 어댑터를 배선한다. 기본은 None(발행 안 함).

    **유튜브는 API 키로 안 된다.** OAuth 데스크톱 클라이언트(`.secrets/client_secret.json`)
    와 브라우저 동의가 필요하고, 그 동의가 만든 `token.json`을 재사용한다
    ([sns.adapters.youtube.auth]). 서버 기동 시점에 자격을 확보하는 이유는, 백그라운드
    스레드에서 동의 창을 띄우면 사용자는 대화만 보고 있어 영영 모르기 때문이다.

    첫 실전은 `privacy_status="private"` 고정이다 — 미감사 API 프로젝트는 어차피
    private 잠금이라 "공개 게시물"은 나오지 않는다.
    """
    target = os.environ.get("CHAT_PUBLISH", "").strip().lower()
    if not target:
        return None
    if target != "youtube":
        raise SystemExit(f"모르는 CHAT_PUBLISH: {target!r} (지원: youtube)")
    client_secret = SECRETS / "client_secret.json"
    if not client_secret.exists():
        raise SystemExit(
            f"중단: {client_secret} 없음 — 유튜브는 API 키로 업로드할 수 없습니다.\n"
            "      GCP 콘솔 > 사용자 인증 정보 > OAuth 클라이언트 ID > **데스크톱 앱**으로\n"
            "      만들어 내려받은 뒤 이 경로에 두세요."
        )
    creds = load_credentials(client_secret, SECRETS / "token.json")
    store = DirMediaStore(OUT)
    # **플랫폼 라우터로 감싼다.** 대기 큐에는 인스타그램 건도 섞여 있는데, 유튜브
    # 어댑터를 그대로 물리면 그 건에서 ValueError가 나 러너 루프 전체가 끊긴다 —
    # 채널 격리(FR-P4)가 정확히 반대로 깨진다.
    return PlatformPublishRouter(
        {"youtube": YouTubePublish(build_youtube(creds), media_bytes=store.get)}
    )


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    load_dotenv(ENV_FILE, override=False)
    try:
        provider = resolve_provider()
    except RuntimeError as exc:
        print(f"중단: {exc}")
        return 1
    key_env = required_key_env(provider)
    if not os.environ.get(key_env):
        print(f"중단: env {key_env} 없음 — 챗봇 본체가 LLM 대화입니다.")
        print("      gemini: https://aistudio.google.com/apikey (무료 티어)")
        print("      openai: https://platform.openai.com/api-keys")
        return 1
    print(f"모델 : {provider} / {resolve_model_name(provider)}")

    ffmpeg = os.environ.get("CHAT_FFMPEG", "ffmpeg")
    style = os.environ.get("CHAT_VIDEO_STYLE", "motion")
    if style not in VIDEO_STYLES:
        print(f"중단: 모르는 CHAT_VIDEO_STYLE: {style!r} (허용: {list(VIDEO_STYLES)})")
        return 1
    formats, missing = wired_formats(ffmpeg)
    methods = wired_methods()
    why = f"  (영상 불가 — {', '.join(missing)})" if missing else ""
    print(f"포맷 : {', '.join(formats)}{why}")
    if "video" in formats:
        print(f"영상 : style={style} / method={', '.join(methods)}")

    publish = make_publish()
    print(f"발행 : {'유튜브 실 업로드(private)' if publish else '없음 — 승인 대기까지만'}")

    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    try:
        conn = psycopg.connect(dsn, connect_timeout=10, autocommit=True)
    except psycopg.OperationalError as exc:
        print(f"중단: PostgreSQL 연결 실패 — docker compose up -d postgres\n      {exc}")
        return 1

    store = PgChatStore(conn)
    approve_base = os.environ.get("APPROVE_WEB_BASE", DEFAULT_APPROVE_BASE)
    app = create_app(
        store,
        model=make_model(),
        start_cycle_fn=make_start_cycle_fn(
            dsn, store, approve_base=approve_base, style=style, ffmpeg=ffmpeg, publish=publish
        ),
        load_media_fn=make_load_media_fn(dsn),
        load_export_fn=make_load_export_fn(dsn),
        channels_fn=make_channels_fn(conn),
        formats=formats,
        methods=methods,
    )
    host = os.environ.get("CHAT_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("CHAT_WEB_PORT", "8003"))
    print(f"키워드 챗봇: http://{host}:{port}/")
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
