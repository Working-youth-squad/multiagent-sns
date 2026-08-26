"""온보딩 프로필 채널의 게시물 생성 사이클 — 인터뷰 결과가 실제 콘텐츠가 되는 연결부.

uv run python scripts/run_profile_cycle.py <채널핸들> [--format shorts|reels|feed_image]

`channel_profile` 최신 revision을 읽어 사이클에 주입한다:
  goal_ref            → run_cycle(goal_ref=...)          (goal 프리셋 실배선)
  categories          → run_cycle(topic_categories=...)  (주제 카테고리 교체)
  brief(주제범위·톤·캐릭터) → channel_brief(Topic) + playbook_guidance(Content)
  캐릭터 이미지        → spec `character_ref`(코너 배지) + 장면 생성 레퍼런스

**채널당 전용 사이클**(targets 1개)로 돈다 — 기존 실험 사이클(변수=mode 하나,
동일 주제 도메인 공유)과 실행 단위를 분리해 통제 설계를 깨지 않는다.

트렌드는 default_service() 그대로 조립한다 — 프로필 맞춤 트렌드(세부주제 쿼리
바인딩)는 트렌드 담당의 상세 버전이 오면 아래 `trends = ...` 한 줄만 교체.

발행은 e2e_cycle과 동일하게 FakePublish로 원장 종결까지만 확인한다(실 어댑터
연결은 발행 러너 운영 배선 몫). 기본 포맷은 플랫폼별 영상(youtube→shorts,
instagram→reels) — 조립은 scripts/e2e_youtube_pipeline.py에서 검증된 것 재사용.

전제: docker compose up -d postgres · env GEMINI_API_KEY · (선택) DATABASE_URL, CARD_FONT
  영상 포맷 추가 전제: TTS 자격증명(GOOGLE_TTS_API_KEY 또는 ADC) · ffmpeg/ffprobe
  캐릭터 장면 생성(선택): 결제 켜진 키 + IMAGE_GEN_MODEL=google:<이미지 모델>
"""

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import psycopg
from dotenv import load_dotenv

from sns.agents.models import make_model
from sns.agents.research import research_topic
from sns.agents.topic import TopicResult
from sns.onboarding.character import make_scene_generate
from sns.onboarding.profile import build_channel_brief
from sns.onboarding.store import PgOnboardingStore
from sns.publish.runner import run_pending_publications
from sns.quality.gate import QualityCheck, QualityReport, check_card
from sns.render.card.media import CardRenderMedia
from sns.render.card.spec import parse_card_spec
from sns.render.images.generate import generate_image
from sns.render.images.resolve import ImageResolution, resolve_images
from sns.render.video.media import VideoRenderMedia
from sns.render.video.quality import check_video
from sns.render.video.tts import synthesize_google
from sns.research.keywords import rank_keywords
from sns.research.sources.llm_grounding import fetch_llm_grounding
from sns.research.trends import SourceFetcher, default_service
from sns.runner.cycle import (
    AssessQuality,
    CycleTarget,
    ResearchTopic,
    ResolveMediaSpec,
    run_cycle,
)
from sns.runner.store import PgCycleStore
from sns.tools.contracts import ContentFormat, MediaAsset, MediaKind, RenderMedia
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
        # file:// URI 되읽기 — image_ref·character_ref를 렌더러가 읽는다.
        return _read_file_uri(url)


def _read_file_uri(url: str) -> bytes:
    return Path(url2pathname(urlparse(url).path)).read_bytes()


def find_font(cli: str | None) -> str | None:
    env = cli or os.environ.get("CARD_FONT")
    if env:
        return env
    return next((c for c in FONT_CANDIDATES if Path(c).exists()), None)


def make_video_gate(media_store: DirMediaStore, ffprobe: str, ffmpeg: str) -> AssessQuality:
    """영상 품질 게이트 — e2e_youtube_pipeline.make_gate와 동형(저장소가 URI라 get 경유)."""

    def assess(
        *, media_spec: Mapping[str, object], media: MediaAsset, content_format: ContentFormat
    ) -> QualityReport:
        report = check_video(media_store.get(media.storage_url), ffprobe=ffprobe, ffmpeg=ffmpeg)
        checks = tuple(QualityCheck(f, False, f) for f in report.failures) or (
            QualityCheck("video_spec", True, "규격·길이·오디오 하한 통과"),
        )
        return QualityReport(status="passed" if report.passed else "failed", checks=checks)

    return assess


def make_channel_keyword_fetcher(subs: Sequence[str]) -> SourceFetcher:
    """세부주제별 자동완성 키워드 랭킹(#37 rank_keywords)을 SourceFetcher 모양으로.

    외부 서비스의 소스별 타임아웃(10s) 안에 끝나야 하므로 세부주제는 앞의 2개,
    내부 타임아웃은 4s로 죈다 — 늦으면 소스째 ok=False로 격리되는 게 규율이다.
    """
    picked = tuple(subs[:2])

    def fetch(limit: int) -> tuple[str, ...]:
        items: list[str] = []
        for sub in picked:
            ranking = rank_keywords(sub, top=max(limit // max(len(picked), 1), 3), timeout_s=4.0)
            items.extend(s.text for s in ranking.candidates)
        return tuple(dict.fromkeys(items))[:limit]

    return fetch


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

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handle", help="온보딩에서 만든 채널 핸들")
    parser.add_argument(
        "--format", choices=("shorts", "reels", "feed_image"), default=None,
        help="콘텐츠 포맷 (기본: youtube→shorts, instagram→reels)",
    )  # fmt: skip
    parser.add_argument(
        "--style", choices=("3col", "motion"), default="motion",
        help="영상 화면 문법. motion(기본): 키워드 타이포+애니메이션 / 3col: 3단 레이아웃. "
        "개발(코드) 주제 채널은 3col을 쓰세요 — motion은 코드 컷을 거부합니다",
    )  # fmt: skip
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 실행 파일 경로")
    parser.add_argument("--font", default=None, help="한글 TTF 경로 (미지정 시 자동 탐색)")
    parser.add_argument(
        "--script-only", action="store_true",
        help="대본까지만 만든다 — 렌더·이미지 해소(과금) 없이 content_item을 승인 대기로 적재",
    )  # fmt: skip
    parser.add_argument(
        "--render-item", default=None, metavar="CONTENT_ITEM_ID",
        help="--script-only로 만든 대본을 영상으로 렌더해 원장을 갱신한다 (사이클 없음)",
    )  # fmt: skip
    args = parser.parse_args()
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

        platform = "instagram" if channel.platform == "instagram" else "youtube"
        fmt: ContentFormat = args.format or ("reels" if platform == "instagram" else "shorts")
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
        font = find_font(args.font)
        renderer: RenderMedia
        assess: AssessQuality | None
        resolve_spec: ResolveMediaSpec | None = None

        if fmt == "feed_image":
            card_renderer = CardRenderMedia(media_store, font_path=font)
            renderer = card_renderer

            def assess_card(
                *,
                media_spec: Mapping[str, object],
                media: MediaAsset,
                content_format: ContentFormat,
            ) -> QualityReport:
                return check_card(parse_card_spec(media_spec), card_renderer.render(media_spec))

            assess = assess_card
        else:
            ffmpeg = args.ffmpeg
            ffprobe = str(Path(ffmpeg).parent / "ffprobe") if ffmpeg != "ffmpeg" else "ffprobe"
            renderer = VideoRenderMedia(
                media_store, synthesize=synthesize_google, font_path=font, ffmpeg=ffmpeg
            )
            assess = make_video_gate(media_store, ffprobe, ffmpeg)

            # 캐릭터가 있으면 장면 생성 레퍼런스 + spec에 character_ref(코너 배지) 주입,
            # 없으면 **일반 생성 이미지로 대체**한다 — 캐릭터 미선택이 글자만 남은
            # 영상(그라데이션 배경)이 되지 않게(image_query의 스톡 폴백은 그대로).
            character_url = profile.character_image_url
            if character_url is not None:
                generate = make_scene_generate(_read_file_uri(character_url))
            else:
                generate = generate_image

            style = args.style

            def resolve_video_spec(spec: Mapping[str, object]) -> ImageResolution:
                res = resolve_images(spec, store=media_store, generate=generate)
                return ImageResolution({**res.media_spec, **_extras()}, res.notes)

            def _extras() -> dict[str, object]:
                extra: dict[str, object] = {}
                if style == "motion":
                    extra["style"] = "motion"
                if character_url is not None:
                    extra["character_ref"] = character_url
                return extra

            resolve_spec = resolve_video_spec

            if args.render_item:
                return render_item(
                    conn, args.render_item,
                    media_store=media_store, renderer=renderer, assess=assess,
                    resolve=resolve_video_spec, fmt=fmt,
                )  # fmt: skip
            if args.script_only:
                # 대본 단계는 렌더·이미지 해소(과금)를 하지 않는다 — 자리표시 자산만
                # 적재하고, 수락 시 --render-item이 진짜 미디어로 갱신한다.
                renderer = placeholder_render
                assess = None
                resolve_spec = lambda spec: ImageResolution({**spec, **_extras()})  # noqa: E731

        # 모션 스타일이면 에이전트에게 화면 문법을 알린다 — 코드/도해 컷은 모션 화면에서
        # 그라데이션으로 강등되므로, 애초에 이미지 장면으로 쓰게 유도한다(soft 지침).
        playbook = brief
        if fmt != "feed_image" and args.style == "motion":
            playbook = brief + (
                "\n영상 화면은 모션 그래픽 스타일이다: code와 concept는 쓰지 말고, "
                "컷마다 image_query(실사 검색어) 또는 image_prompt로 배경 장면을 지정하라. "
                "화면 글자는 최소로 — subtitle은 2~4단어 키워드, narration은 짧은 한 문장."
            )

        # 프로필 맞춤 트렌드 — 채널 세부주제를 소스에 묶는다. 상세 조립(소스 선별·
        # 밴드 파라미터)은 트렌드 담당 몫이라 이 블록만 교체하면 된다.
        #   channel_keywords: 팀원의 질의어 기반 자동완성 랭킹(#37, 무인증) — 세부주제
        #     주변에서 실제로 검색되는 키워드가 주제 후보 풀에 들어간다.
        #   llm_grounding_channel: 채널 주제로 물은 검색 그라운딩(문장형 후보, 유료 1회).
        extra: dict[str, SourceFetcher] = {
            "channel_keywords": make_channel_keyword_fetcher(profile.topic_subs)
        }
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if gemini_key:
            channel_topic = f"{profile.topic_major} {' '.join(profile.topic_subs)}"

            def _grounded(limit: int) -> tuple[str, ...]:
                return fetch_llm_grounding(limit, api_key=gemini_key, topic=channel_topic)

            extra["llm_grounding_channel"] = _grounded
        trends = default_service(extra_sources=extra)

        # 근거 노트 리서치 — 확정된 주제의 사실·수치를 대본이 인용할 수 있게(FR-Q7 정합).
        research_fn: ResearchTopic | None = None
        if gemini_key:

            def _research(topic: TopicResult) -> str:
                return research_topic(topic.title, api_key=gemini_key)

            research_fn = _research

        result = run_cycle(
            PgCycleStore(conn),
            goal_ref=profile.goal_ref,
            targets=[
                CycleTarget(
                    channel_id=channel.channel_id,
                    platform=platform,
                    content_format=fmt,
                    mode="hybrid" if channel.mode == "hybrid" else "auto",
                )
            ],
            model=make_model(),
            research_trends=trends,
            read_stats=FakeReadStats(),
            render_media=renderer,
            assess_quality=assess,
            resolve_media_spec=resolve_spec,
            research_topic=research_fn,
            audience=profile.topic_major,
            channel_brief=brief,
            topic_categories=profile.categories,
            playbook_guidance=playbook,
        )
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
