"""온보딩 프로필 채널의 게시물 생성 사이클 — 인터뷰 결과가 실제 콘텐츠가 되는 연결부.

uv run python scripts/run_profile_cycle.py <채널핸들> [--format card|video]
                                                     [--method template|generated_scene]
                                                     [--style 3col|motion]
                                                     [--script-only | --render-item <id>]

`channel_profile` 최신 revision을 읽어 사이클에 주입한다:
  goal_ref            → run_cycle(goal_ref=...)          (goal 프리셋 실배선)
  categories          → run_cycle(topic_categories=...)  (주제 카테고리 교체)
  brief(주제범위·톤·캐릭터) → channel_brief(Topic) + playbook_guidance(Content)
  캐릭터 이미지        → spec `character_ref`(코너 배지) + 장면 생성 레퍼런스

**채널당 전용 사이클**(targets 1개)로 돈다 — 기존 실험 사이클(변수=mode 하나,
동일 주제 도메인 공유)과 실행 단위를 분리해 통제 설계를 깨지 않는다.

트렌드는 프로필에 맞춰 조립한다 — 소스 목록은 topic_major 파생(개발 전용 소스는
비개발 채널에서 빠진다), 검색어는 (topic_major, *topic_subs)를 그대로 쓴다.

발행은 e2e_cycle과 동일하게 FakePublish로 원장 종결까지만 확인한다(실 어댑터
연결은 발행 러너 운영 배선 몫).

포맷은 `--format`으로 고른다 — card=피드 카드, video=쇼츠(유튜브)/릴스(인스타).
영상은 ffmpeg과 Google TTS가 필요하고, 정사각 사진 해소(resolve_images)가 함께 돈다.

**영상은 축이 둘이다.** `--method`는 어느 트랙인가(재료 출처), `--style`은 그 트랙 안의
화면 문법이다. `--method generated_scene`은 **컷마다 유료 이미지를 생성한다**(사이클당
12장 상한, FR-P6). 기본은 template이라 명시해야 켜진다 — 라우터에 안 적힌 방식은
에이전트가 고를 수도 없다(Capability Gate).

렌더 배선(렌더러·게이트·해소)은 [sns.runner.wiring]이 정본이다 — 챗봇 웹
(run_chat_web.py)도 같은 함수를 부른다. 이 파일에 배선을 다시 적으면 한쪽만
고쳐진다(그 사고가 실제로 있었다: 옛 블록이 `--style 3col`에서 영상 라우터를
카드 렌더러로 덮어썼다).

전제: docker compose up -d postgres · env GEMINI_API_KEY · (선택) DATABASE_URL, CARD_FONT
  영상 포맷 추가 전제: TTS 자격증명(GOOGLE_TTS_API_KEY 또는 ADC) · ffmpeg/ffprobe
  캐릭터 장면 생성(선택): 결제 켜진 키 + IMAGE_GEN_MODEL=google:<이미지 모델>
"""

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import psycopg
from dotenv import load_dotenv

from sns.agents.models import make_model
from sns.onboarding.profile import build_channel_brief
from sns.onboarding.store import PgOnboardingStore
from sns.onboarding.trends import profile_trend_service
from sns.publish.runner import run_pending_publications
from sns.runner.cycle import AssessQuality, ChannelMode, CycleTarget, ResolveMediaSpec, run_cycle
from sns.runner.formats import PLATFORMS, parse_platform
from sns.runner.formats import content_format_for as format_for
from sns.runner.store import PgCycleStore
from sns.runner.wiring import build_render_wiring, extras_only_resolve, style_guidance
from sns.tools.contracts import (
    ContentFormat,
    MediaAsset,
    MediaKind,
    Platform,
    RenderMedia,
    ResearchTrends,
    SourceResult,
    TrendDigest,
    VideoMethod,
)
from sns.tools.fakes import FakePublish, FakeReadStats
from sns.web.approve.store import ApprovalNotFound, PgApprovalStore

OUT = Path(__file__).parent / "out"
ENV_FILE = Path(__file__).parent.parent / ".env"
DEFAULT_DSN = "postgresql://sns:sns@localhost:5432/sns"
FONT_CANDIDATES = (
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
)

_CHANNEL_MODES: dict[str, ChannelMode] = {"auto": "auto", "hybrid": "hybrid", "manual": "manual"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="온보딩 프로필 채널의 게시물 생성 사이클")
    p.add_argument("handle", help="채널 핸들")
    p.add_argument("--format", choices=("card", "video"), default="card")
    p.add_argument("--ffmpeg", default="ffmpeg", help="영상 포맷에서만 쓴다")
    p.add_argument("--font", default=None, help="자막/카드 폰트 경로(비우면 자동 탐색)")
    p.add_argument(
        "--method",
        choices=("template", "generated_scene"),
        default="template",
        help="generated_scene은 컷마다 유료 이미지 생성이다(사이클당 12장 상한)",
    )
    # `--method`와 직교한다: method=어느 트랙(재료 출처), style=그 트랙 안의 화면 문법.
    p.add_argument(
        "--style", choices=("3col", "motion"), default="motion",
        help="영상 화면 문법. motion(기본): 키워드 타이포+애니메이션 / 3col: 3단 레이아웃. "
        "개발(코드) 주제 채널은 3col을 쓰세요 — motion은 코드 컷을 거부합니다",
    )  # fmt: skip
    p.add_argument(
        "--script-only", action="store_true",
        help="대본까지만 만든다 — 렌더·이미지 해소(과금) 없이 content_item을 승인 대기로 적재",
    )  # fmt: skip
    p.add_argument(
        "--render-item", default=None, metavar="CONTENT_ITEM_ID",
        help="--script-only로 만든 대본을 영상으로 렌더해 원장을 갱신한다 (사이클 없음)",
    )  # fmt: skip
    return p


def platform_of(platform: str) -> Platform:
    """채널 플랫폼을 검증해 통과시킨다. 조용한 폴백을 두지 않는다.

    매핑 자체는 [sns.runner.formats]가 정본이다 — 여기는 **CLI의 판정**만 얹는다
    (모르는 값이면 종료). 웹 서버는 같은 매핑을 쓰되 종료하지 않는다.
    """
    found = parse_platform(platform)
    if found is None:
        raise SystemExit(f"모르는 플랫폼: {platform!r} (허용: {sorted(PLATFORMS)})")
    return found


def content_format_for(platform: Platform, fmt: str) -> ContentFormat:
    """플랫폼 × 요청 포맷 → ContentFormat ([sns.runner.formats] 정본에 위임).

    `fmt`는 argparse가 card|video로 좁힌 값이라 여기서 다시 검증하지 않는다.
    """
    return format_for(platform, "card" if fmt == "card" else "video")


def channel_mode_of(mode: str) -> ChannelMode:
    """채널 mode를 그대로 통과시키되 모르는 값은 거부한다.

    예전에는 `"hybrid" if mode == "hybrid" else "auto"`였다 — **manual 채널이 auto로
    승격돼 자동 발행 경로로 들어갔다.** manual은 기계 발행 대상이 아니다
    ([sns.publish.modes.MACHINE_MODES]). 조용한 폴백은 오타 하나로 발행 모드를 바꾼다.
    """
    try:
        return _CHANNEL_MODES[mode]
    except KeyError:
        raise SystemExit(f"모르는 채널 mode: {mode!r} (허용: {sorted(_CHANNEL_MODES)})") from None


class DirMediaStore:
    """렌더 바이트를 디스크에 — 사람이 열어볼 수 있게(scripts/e2e_cycle.py와 동형)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.saved: list[Path] = []
        root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, *, checksum: str, kind: MediaKind, ext: str) -> str:
        path = self.root / f"{kind}-{checksum[:16]}.{ext}"
        path.write_bytes(data)
        self.saved.append(path)
        return path.resolve().as_uri()

    def get(self, url: str) -> bytes:
        """`put`이 낸 file:// URI를 되읽는다.

        영상 품질 게이트가 산출 mp4를 다시 읽고([sns.render.video.quality]), 사진 해소도
        저장한 정사각을 되읽고, 렌더러가 image_ref·character_ref를 읽는다. URI를 경로로
        그냥 넘기면 Windows에서 `file:\\C:\\...`가 되어 OSError가 난다 — 실제로 그렇게 터졌다.
        """
        return _read_file_uri(url)


class ReportingTrends:
    """트렌드 서비스를 감싸 소스별 결과를 기억한다 — 죽은 소스를 드러내려고.

    소스 격리(FR-G4)는 실패를 `ok=False`로 삼킨다. 설계대로지만, 그 때문에 모델이
    은퇴해 소스 하나가 오래 죽어 있어도 아무도 알아채지 못했다(gemini-2.0-flash가
    실제로 그랬다). **표시하려고 트렌드를 한 번 더 부르지는 않는다** — 사이클이 부르는
    그 호출의 결과를 그대로 들고 있다가 끝나고 보여준다.
    """

    def __init__(self, inner: ResearchTrends) -> None:
        self._inner = inner
        self.results: tuple[SourceResult, ...] = ()

    def __call__(self, sources: tuple[str, ...] | None = None, limit: int = 10) -> TrendDigest:
        digest = self._inner(sources, limit)
        self.results = digest.source_results
        return digest

    def report(self) -> str:
        if not self.results:
            return "  (트렌드 조회 없음)"
        rows = []
        for r in self.results:
            mark = f"{len(r.items)}건" if r.ok and r.items else ("빈 결과" if r.ok else "실패")
            rows.append(f"  {r.source}: {mark}")
        return "\n".join(rows)


def _read_file_uri(url: str) -> bytes:
    return Path(url2pathname(urlparse(url).path)).read_bytes()


def find_font(cli: str | None) -> str | None:
    env = cli or os.environ.get("CARD_FONT")
    if env:
        return env
    return next((c for c in FONT_CANDIDATES if Path(c).exists()), None)


# 대본 단계의 자리표시 자산 URL — --render-item이 진짜 미디어로 갱신하기 전의 표식.
PENDING_URL = "pending://script-approval"


def placeholder_render(media_spec: Mapping[str, object], kind: MediaKind) -> MediaAsset:
    """--script-only의 렌더 자리표시 — 렌더 없이 원장 FK 사슬(자산→발행)만 세운다."""
    checksum = hashlib.sha256(
        json.dumps(media_spec, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return MediaAsset(kind=kind, storage_url=PENDING_URL, checksum=checksum)


def render_item(
    conn: psycopg.Connection,
    item_id: str,
    *,
    media_store: "DirMediaStore",
    renderer: RenderMedia,
    assess: AssessQuality | None,
    resolve: ResolveMediaSpec,
    fmt: ContentFormat,
) -> int:
    """수락된 대본 1건 → 이미지 해소 → 렌더 → 게이트 → 원장 갱신 (사이클 없음)."""
    row = conn.execute("SELECT media_spec FROM content_item WHERE id = %s", (item_id,)).fetchone()
    if row is None or not isinstance(row[0], Mapping):
        print(f"중단: content_item {item_id!r} 없음 또는 media_spec 비어 있음")
        return 1

    resolution = resolve(row[0])
    for note in resolution.notes:
        print(f"  ⚠ {note}")
    media = renderer(resolution.media_spec, "video")
    report: Mapping[str, object] | None = None
    quality_status = "needs_review"
    if assess is not None:
        gate = assess(media_spec=resolution.media_spec, media=media, content_format=fmt)
        quality_status, report = gate.status, gate.to_json()
    try:
        # 승인 웹 재렌더와 같은 원장 경로 — 항목은 종결되지 않고 다시 승인 대기가 된다.
        PgApprovalStore(conn).update_media(
            item_id,
            media_spec=resolution.media_spec,
            storage_url=media.storage_url,
            checksum=media.checksum,
            quality_status=quality_status,
            quality_report=report,
        )
    except ApprovalNotFound:
        print(f"중단: {item_id!r}가 승인 대기 목록에 없음 — 이미 처리됐거나 hybrid가 아님")
        return 1
    print(f"  품질: {quality_status}")
    for p in media_store.saved:
        print(f"  렌더 산출물: {p}")
    return 0 if quality_status == "passed" else 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args()
    handle = args.handle

    load_dotenv(ENV_FILE, override=False)
    if not os.environ.get("GEMINI_API_KEY"):
        print("중단: env GEMINI_API_KEY 없음 — https://aistudio.google.com/apikey")
        return 1

    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    try:
        conn = psycopg.connect(dsn, connect_timeout=10, autocommit=True)
    except psycopg.OperationalError as exc:
        print(f"중단: PostgreSQL 연결 실패 — docker compose up -d postgres\n      {exc}")
        return 1

    with conn:
        onboarding = PgOnboardingStore(conn)
        channel = next((c for c in onboarding.list_channels() if c.handle == handle), None)
        if channel is None:
            print(f"중단: 채널 {handle!r} 없음 — 온보딩 웹(run_onboarding_web.py)에서 먼저 생성")
            return 1
        profile = onboarding.latest_profile(channel.channel_id)
        if profile is None:
            print(f"중단: 채널 {handle!r}에 프로필 없음 — 온보딩 인터뷰를 먼저 완료")
            return 1

        platform = platform_of(channel.platform)
        fmt = content_format_for(platform, args.format)
        brief = build_channel_brief(profile)
        print(f"채널  : {channel.handle} ({channel.platform}, {channel.mode}, {fmt})")
        print(f"프로필: {profile.topic_major} / {', '.join(profile.topic_subs)}")
        print(f"goal  : {profile.goal_ref}")
        print(f"캐릭터: {profile.character_image_url or '없음'}")
        print(f"brief :\n{brief}\n")

        if (args.script_only or args.render_item) and fmt == "feed_image":
            print("중단: --script-only / --render-item은 영상 포맷 전용입니다")
            return 1

        media_store = DirMediaStore(OUT)
        font = find_font(args.font)  # --font를 받아놓고 무시하지 않는다

        # 배선 정본은 [sns.runner.wiring]이다 — 챗봇 웹도 같은 함수를 부른다.
        # 예전엔 이 블록이 여기 있었고, 옛 판이 지워지지 않은 채 뒤에 남아
        # `--format video --style 3col`이 영상 라우터를 카드 렌더러로 덮어썼다.
        methods: tuple[VideoMethod, ...] = ("template",)
        if args.method == "generated_scene":
            # 생성은 유료라 --method로 명시해야 등록된다(Capability Gate).
            methods = ("template", "generated_scene")
        wiring = build_render_wiring(
            kind="card" if fmt == "feed_image" else "video",
            store=media_store,
            topic_major=profile.topic_major,
            font=font,
            style=args.style,
            methods=methods,
            character_image_url=profile.character_image_url,
            character_style=profile.character_style,
            ffmpeg=args.ffmpeg,
        )
        renderer: RenderMedia = wiring.render_media
        assess: AssessQuality | None = wiring.assess_quality
        resolve: ResolveMediaSpec | None = wiring.resolve_media_spec
        methods = wiring.supported_methods

        if fmt != "feed_image":
            if args.render_item:
                # 영상 배선은 해소를 반드시 낸다 — None이면 배선이 깨진 것이라 조용히
                # 넘기지 않는다(정지 화면만 남은 mp4가 나온다).
                assert resolve is not None, "영상 배선에 resolve가 없다"
                return render_item(
                    conn, args.render_item,
                    media_store=media_store, renderer=renderer, assess=assess,
                    resolve=resolve, fmt=fmt,
                )  # fmt: skip
            if args.script_only:
                # 대본 단계는 렌더·이미지 해소(과금)를 하지 않는다 — 자리표시 자산만
                # 적재하고, 수락 시 --render-item이 진짜 미디어로 갱신한다.
                renderer = placeholder_render
                assess = None
                resolve = extras_only_resolve(
                    style=args.style, character_image_url=profile.character_image_url
                )

        # 화면 문법 지침도 배선과 같은 곳에서 온다([sns.runner.wiring]).
        guidance = style_guidance(args.style) if fmt != "feed_image" else ""
        playbook = f"{brief}\n{guidance}" if guidance else brief

        # 프로필 맞춤 트렌드 — 조립은 온보딩 화면 6과 같은 함수를 쓴다(단일 출처).
        trends = profile_trend_service(profile)
        search_terms = (profile.topic_major, *profile.topic_subs)
        reporting = ReportingTrends(trends)
        print(f"트렌드: {', '.join(trends.sources)}")
        print(f"검색어: {', '.join(search_terms)}\n")

        result = run_cycle(
            PgCycleStore(conn),
            goal_ref=profile.goal_ref,
            topic_major=profile.topic_major,
            targets=[
                CycleTarget(
                    channel_id=channel.channel_id,
                    platform=platform,
                    content_format=fmt,
                    mode=channel_mode_of(channel.mode),
                )
            ],
            model=make_model(),
            research_trends=reporting,
            read_stats=FakeReadStats(),
            render_media=renderer,
            supported_methods=methods,
            assess_quality=assess,
            resolve_media_spec=resolve,
            channel_brief=brief,
            topic_categories=profile.categories,
            playbook_guidance=playbook,
        )
        print("트렌드 소스 결과")
        print(reporting.report())
        print(f"cycle={result.cycle_id} status={result.status}")
        for t in result.targets:
            print(f"  {t.channel_id[:8]}... -> {t.outcome} {t.error or ''}")
        for p in media_store.saved:
            print(f"  렌더 산출물: {p}")
        if result.status != "completed":
            return 1

        if channel.mode == "hybrid":
            print("\nhybrid 채널 — 승인 화면(run_approve_web.py)에서 검수 후 발행됩니다.")
        else:
            print("\n발행 러너 (FakePublish — 원장 전이 확인)")
            for o in run_pending_publications(conn, FakePublish()):
                print(f"  {o}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
