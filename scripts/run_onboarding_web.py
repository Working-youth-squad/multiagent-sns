"""온보딩 인터뷰 웹 서버 기동. uv run python scripts/run_onboarding_web.py

전제:
  1. docker compose up -d postgres (+ python -m sns.db.migrate)
  2. env DATABASE_URL — (선택) 기본 postgresql://sns:sns@localhost:5432/sns
  3. env ONBOARD_WEB_HOST/PORT — (선택) 기본 127.0.0.1:8002
  4. (선택) GEMINI_API_KEY — 있으면 트렌드 추천·줄글 미세조정·캐릭터 생성이 켜진다.
     없으면 인터뷰·프로필 저장만 동작한다(협력자 셋 다 탈부착 가능).

단일 커넥션 전제(scripts/run_approve_web.py와 동일 규율).
"""

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import psycopg
import uvicorn
from dotenv import load_dotenv

from sns.web.onboarding.app import create_app
from sns.web.onboarding.render import ScriptJobView, VideoItemView

ROOT = Path(__file__).parent.parent
ENV_FILE = ROOT / ".env"
DEFAULT_DSN = "postgresql://sns:sns@localhost:5432/sns"
CHAR_DIR = Path(__file__).parent / "out" / "characters"
LOG_DIR = Path(__file__).parent / "out" / "cycle-logs"


class DirMediaStore:
    """캐릭터 PNG를 디스크에 떨어뜨린다(scripts/e2e_cycle.py와 동형) — 재기동에도 남게."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, *, checksum: str, kind: str, ext: str) -> str:
        path = self.root / f"{kind}-{checksum[:16]}.{ext}"
        path.write_bytes(data)
        return path.resolve().as_uri()

    def get(self, url: str) -> bytes:
        from urllib.parse import urlparse
        from urllib.request import url2pathname

        return Path(url2pathname(urlparse(url).path)).read_bytes()


def _spawn(log: Path, *cli_args: str) -> subprocess.Popen[bytes]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(log, "wb") as f:  # 자식이 핸들을 상속하므로 부모 쪽은 바로 닫는다
        return subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "run_profile_cycle.py"), *cli_args],
            stdout=f,
            stderr=subprocess.STDOUT,
            cwd=ROOT,
        )


def _tail(log: Path) -> str:
    return log.read_text(encoding="utf-8", errors="replace")[-2000:] if log.exists() else ""


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


# --script-only가 남기는 자리표시 자산 URL(run_profile_cycle.PENDING_URL과 동일 값).
_PENDING_URL = "pending://script-approval"

_ITEMS_SQL = """
SELECT ci.id, t.title, ci.media_spec, COALESCE(ci.body, ''), ma.storage_url
  FROM content_item ci
  JOIN publication p ON p.content_item_id = ci.id
  JOIN channel ch ON ch.id = p.channel_id
  JOIN topic t ON t.id = ci.topic_id
  LEFT JOIN media_asset ma ON ma.content_item_id = ci.id
 WHERE ch.handle = %s AND ci.status != 'rejected'
 ORDER BY ci.created_at DESC
"""


class SubprocessVideoManager:
    """영상 관리 탭 백엔드 — `run_profile_cycle.py`를 단계별 서브프로세스로 돌린다.

    대본: `--script-only`(과금 없음, LLM만), 렌더: `--render-item <id>`(이미지·TTS).
    항목 목록은 원장(Postgres)에서 읽는다 — 서버가 재시작돼도 대본·영상은 남는다.
    ponytail: 프로세스 핸들만 메모리라 재시작 시 '진행 중' 표시를 잃는다(로그는 파일).
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._scripts: dict[str, tuple[subprocess.Popen[bytes], Path]] = {}
        self._renders: dict[str, tuple[subprocess.Popen[bytes], Path]] = {}

    def start_script(self, handle: str) -> None:
        job = self._scripts.get(handle)
        if job is not None and job[0].poll() is None:
            return  # 이미 진행 중 — 중복 기동 방지
        log = LOG_DIR / f"script-{_safe(handle)}.log"
        self._scripts[handle] = (_spawn(log, handle, "--script-only"), log)

    def start_render(self, handle: str, item_id: str) -> None:
        job = self._renders.get(item_id)
        if job is not None and job[0].poll() is None:
            return
        log = LOG_DIR / f"render-{_safe(item_id)}.log"
        self._renders[item_id] = (_spawn(log, handle, "--render-item", item_id), log)

    def script_job(self, handle: str) -> ScriptJobView | None:
        job = self._scripts.get(handle)
        if job is None:
            return None
        proc, log = job
        if proc.poll() is None:
            return ScriptJobView(state="running")
        if proc.returncode == 0:
            return None  # 성공 — 항목 목록에 대본이 떴다
        return ScriptJobView(state="failed", log_tail=_tail(log))

    def items(self, handle: str) -> tuple[VideoItemView, ...]:
        with psycopg.connect(self._dsn, connect_timeout=10) as conn:
            rows = conn.execute(_ITEMS_SQL, (handle,)).fetchall()
        return tuple(self._to_view(r) for r in rows)

    def _to_view(self, row: tuple[object, ...]) -> VideoItemView:
        item_id, title, spec, body, storage_url = row
        slides: tuple[tuple[str, str], ...] = ()
        if isinstance(spec, dict) and isinstance(spec.get("slides"), list):
            slides = tuple(
                (str(s.get("subtitle", "")), str(s.get("narration", "")))
                for s in spec["slides"]
                if isinstance(s, dict)
            )
        url = str(storage_url or "")
        state, video_path, log_tail = "script", "", ""
        job = self._renders.get(str(item_id))
        if job is not None and job[0].poll() is None:
            state = "rendering"
        elif url and url != _PENDING_URL:
            state = "done"
            path = (
                Path(url2pathname(urlparse(url).path)) if url.startswith("file://") else Path(url)
            )
            video_path = str(path)
        elif job is not None and job[0].returncode != 0:
            state, log_tail = "failed", _tail(job[1])
        return VideoItemView(
            item_id=str(item_id), topic=str(title), state=state, slides=slides,
            body=str(body), video_path=video_path, log_tail=log_tail,
        )  # fmt: skip


def main() -> int:
    load_dotenv(ENV_FILE, override=False)
    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    try:
        conn = psycopg.connect(dsn, connect_timeout=10, autocommit=True)
    except psycopg.OperationalError as exc:
        print(f"중단: PostgreSQL 연결 실패 — docker compose up -d postgres\n      {exc}")
        return 1

    from sns.agents.models import make_model
    from sns.onboarding.character import make_character_fn
    from sns.onboarding.recommend import make_recommend_fn, make_refine_fn
    from sns.onboarding.store import PgOnboardingStore

    recommend_fn = None
    refine_fn = None
    if os.environ.get("GEMINI_API_KEY"):
        model = make_model()
        recommend_fn = make_recommend_fn(model)
        refine_fn = make_refine_fn(model)
    # 캐릭터 생성은 항상 배선 — 키·할당량이 없으면 ImageGenerationError가 나고
    # 웹 앱이 "캐릭터 없음"으로 온보딩을 계속한다(비용 통제는 character.py 몫).
    ensure_character_fn = make_character_fn(DirMediaStore(CHAR_DIR))

    app = create_app(
        PgOnboardingStore(conn),
        recommend_fn=recommend_fn,
        refine_fn=refine_fn,
        ensure_character_fn=ensure_character_fn,
        video_manager=SubprocessVideoManager(dsn),
    )
    host = os.environ.get("ONBOARD_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("ONBOARD_WEB_PORT", "8002"))
    print(f"온보딩 인터뷰: http://{host}:{port}/")
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
