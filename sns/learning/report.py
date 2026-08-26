"""분석글·플레이북 착지 (FR-L4·L5, M6) — 저장된 관측에서 원장까지의 마지막 한 칸.

`sns/agents/analyst.py`는 이미 완성돼 있었다. 없던 것은 **그 앞뒤**다: 무엇을 표본으로
줄 것인가(앞), 나온 글과 지침을 어디에 앉힐 것인가(뒤). 그 둘이 없어서 지금까지 분석은
`scripts/e2e_analyst.py`에서 가짜 store로만 돌았고 `analysis_note`·`playbook`은 비어
있었다.

## 이 모듈이 지키는 것

- **분석은 API를 다시 때리지 않는다.** `poll_metrics` 자리에 실 어댑터가 아니라
  `StoredMetrics`를 넣는다. 실 어댑터를 물리면 게시물 하나 분석에 (기준선 30건 × 창
  1개)만큼 쿼터가 나가고, 더 나쁘게는 **폴링 시점과 값이 갈린다** — 같은 창을 두 번
  읽었는데 숫자가 다르면 그건 창이 아니다([sns.learning.observations]).
- **검증기가 거부한 것은 아무것도 남기지 않는다.** 글도, 지침도. 거부 사실만
  `run_event(kind='error')`로 남는다. 지어낸 인용이 원장에 들어가면 그게 다음 사이클의
  근거가 된다.
- **수치는 코드가, 서술은 LLM이**(FR-L5). 이 모듈은 표본을 고를 뿐 숫자를 만들지 않는다.

## 왜 플레이북 쓰기를 미루나 (`_DeferredPlaybook`)

에이전트의 `write_playbook_tool`은 LLM이 부르는 **즉시** 저장소에 쓴다. 그런데 검증은
마지막 메시지가 나온 **뒤**에 돈다. 그대로 두면 "인용 없는 수치로 거부된 분석"이 남긴
지침이 플레이북에 살아남고, 그 지침은 다음 사이클 콘텐츠 생성에 그대로 들어간다(FR-L4).
거부된 글은 안 남기면서 그 글이 낳은 지침은 남기는 것은 앞뒤가 맞지 않는다.

그래서 쓰기를 버퍼에 모았다가 **검증 통과 후에만** 흘려보낸다. 대가는 하나다: 툴 응답에
찍히는 버전 번호가 이번 실행 안의 순번이라 DB가 최종적으로 매기는 번호와 다를 수 있다.
정확한 번호를 보여주려면 `MetricStore`에 플레이북 읽기가 있어야 하고 그건 동결된 seam을
바꾸는 일이다(PR + 상대 리뷰). 지침의 무결성이 툴 응답 문자열의 정확도보다 무겁다.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

from langchain_core.language_models import BaseChatModel

from sns.agents.analyst import AnalysisRejected, run_analysis
from sns.learning.observations import StoredMetrics
from sns.learning.schedule import REWARD_WINDOW_INDEX
from sns.learning.stores import MetricStore
from sns.signals.scoreboard import MIN_BASELINE_N, MIN_REGRESSION_N
from sns.tools.contracts import Platform, PlaybookScope, PlaybookVersion

# 기준선에 넣을 **최근** 게시물 수의 상한. 회귀 허용선(30건)과 같은 값인 이유는 둘이다:
# 그보다 적으면 스코어보드가 계속 `small_sample`이고, 그보다 많이 읽어도 중앙값은 거의
# 움직이지 않으면서 관측 조회만 건수만큼 늘어난다.
# 최근 것으로 자르는 것이 핵심이다 — 1년 전 게시물이 중앙값을 끌면 그건 지금 이 계정의
# 기준선이 아니다.
DEFAULT_BASELINE_LIMIT = MIN_REGRESSION_N
# 발행 원장을 한 번에 훑는 상한(StoredMetrics 색인 크기).
DEFAULT_LEDGER_LIMIT = 500
# 거부된 본문을 run_event에 남길 때의 길이 상한 — 거부율 분석에는 앞머리면 충분하고,
# 원장은 로그 저장소가 아니다.
REJECTED_BODY_CHARS = 500


@dataclass(frozen=True)
class AnalysisSample:
    """이번 분석의 표본 — 마지막이 분석 대상, 앞이 기준선(`run_analysis` 계약)."""

    platform: Platform
    window_index: int
    target_post_id: str
    baseline_post_ids: tuple[str, ...] = ()

    @property
    def post_ids(self) -> tuple[str, ...]:
        return (*self.baseline_post_ids, self.target_post_id)

    @property
    def verdict_available(self) -> bool:
        """기준선이 성립하는가 — 아니면 글은 '판정 불가'로 나온다(코드가 결정한다).

        스코어보드의 판정과 같은 문턱을 본다. 여기서 미리 아는 이유는 하나뿐이다:
        CLI가 에이전트를 부르기 전에 무엇이 나올지 운영자에게 알려 준다(`--dry-run`).
        """
        return len(self.baseline_post_ids) >= MIN_BASELINE_N


@dataclass(frozen=True)
class NoteReport:
    """한 플랫폼 1회 분석의 결말. 넷 중 하나다: 착지 · 거부 · 실패 · 건너뜀."""

    platform: Platform
    window_index: int
    target_post_id: str | None = None
    baseline_count: int = 0
    note_id: str | None = None
    insufficient_evidence: bool = False
    playbook_written: bool = False
    rejected_reasons: tuple[str, ...] = ()
    error: str | None = None
    skipped: str | None = None  # 표본이 없어 에이전트를 부르지도 않은 경우

    def summary(self) -> str:
        if self.skipped:
            return f"{self.platform}: 건너뜀 — {self.skipped}"
        if self.error:
            return f"{self.platform}: 실패 — {self.error}"
        if self.rejected_reasons:
            return f"{self.platform}: 검증기 거부 — {'; '.join(self.rejected_reasons)}"
        verdict = "판정 불가" if self.insufficient_evidence else "판정 있음"
        playbook = " · 플레이북 갱신" if self.playbook_written else ""
        return (
            f"{self.platform}: 분석글 적재({self.note_id}) · 대상 {self.target_post_id} · "
            f"기준선 {self.baseline_count}건 · {verdict}{playbook}"
        )


def select_sample(
    stored: StoredMetrics,
    platform: Platform,
    *,
    window_index: int = REWARD_WINDOW_INDEX,
    baseline_limit: int = DEFAULT_BASELINE_LIMIT,
) -> AnalysisSample | None:
    """그 창이 **이미 찍힌** 게시물만으로 표본을 만든다. 하나도 없으면 None.

    최신 1건이 분석 대상, 그 앞의 최근 `baseline_limit`건이 기준선이다.

    `available_posts`가 돌려주는 순서는 발행 원장의 순서(발행 시각 오름차순)를 그대로
    물려받는다 — `MetricStore.published_items`가 발행순으로 주고 `StoredMetrics`가 그
    순서대로 색인을 만들기 때문이다. '마지막 = 최신'은 그 위에서만 성립하므로 테스트가
    그 순서를 못박아 둔다(tests/test_analysis_report.py).
    """
    posts = stored.available_posts(platform, window_index=window_index)
    if not posts:
        return None
    *older, target = posts
    baseline = tuple(older[-baseline_limit:]) if baseline_limit > 0 else ()
    return AnalysisSample(
        platform=platform,
        window_index=window_index,
        target_post_id=target,
        baseline_post_ids=baseline,
    )


@dataclass
class _DeferredPlaybook:
    """`WritePlaybook` 계약을 만족하되 **버퍼에만** 쌓는다 — 모듈 docstring 참조."""

    pending: list[tuple[PlaybookScope, str, str | None]] = field(default_factory=list)

    def __call__(
        self, scope: PlaybookScope, guidance: str, scope_ref: str | None = None
    ) -> PlaybookVersion:
        self.pending.append((scope, guidance, scope_ref))
        # 이번 실행 안의 순번. 진짜 버전은 flush 때 저장소가 매긴다.
        provisional = sum(1 for s, _, ref in self.pending if s == scope and ref == scope_ref)
        return PlaybookVersion(scope=scope, scope_ref=scope_ref, version=provisional)

    def flush(self, store: MetricStore) -> int:
        for scope, guidance, scope_ref in self.pending:
            store.save_playbook(scope, guidance, scope_ref)
        return len(self.pending)


def write_analysis_note(
    model: BaseChatModel,
    store: MetricStore,
    *,
    platform: Platform,
    window_index: int = REWARD_WINDOW_INDEX,
    cycle_id: str | None = None,
    baseline_limit: int = DEFAULT_BASELINE_LIMIT,
    since: datetime | None = None,
    ledger_limit: int = DEFAULT_LEDGER_LIMIT,
    stored: StoredMetrics | None = None,
) -> NoteReport:
    """저장된 관측 → 스코어보드 → Analyst → 검증 통과분만 적재. 예외를 던지지 않는다.

    `stored`를 주입하면 원장을 다시 훑지 않는다 — 여러 플랫폼이 한 스냅샷을 보게 하는
    손잡이다(`write_analysis_notes`).
    """
    stored = stored if stored is not None else StoredMetrics(store, since=since, limit=ledger_limit)
    sample = select_sample(
        stored, platform, window_index=window_index, baseline_limit=baseline_limit
    )
    if sample is None:
        # 원장에 적지 않는다: 표본이 없다는 사실은 결정론이라 매 실행이 같은 줄을 쓴다
        # (놓친 창을 세기만 하는 폴러와 같은 이유 — sns/learning/poller.py).
        return NoteReport(
            platform=platform,
            window_index=window_index,
            skipped=f"창 {window_index}이 찍힌 게시물이 없다 (먼저 run_metrics_poll)",
        )

    playbook = _DeferredPlaybook()
    base = NoteReport(
        platform=platform,
        window_index=window_index,
        target_post_id=sample.target_post_id,
        baseline_count=len(sample.baseline_post_ids),
    )
    try:
        result = run_analysis(
            model,
            platform=platform,
            post_ids=sample.post_ids,
            window_index=window_index,
            poll_metrics=stored,  # ← 실 어댑터를 물리지 않는 자리(모듈 docstring)
            read_stats=store.read_topic_stats,
            write_playbook=playbook,
        )
    except AnalysisRejected as exc:
        _log(
            store,
            cycle_id=cycle_id,
            reason="analysis_rejected",
            sample=sample,
            extra={
                "reasons": list(exc.reasons),
                "body_head": exc.body[:REJECTED_BODY_CHARS],
                # 몇 건의 지침이 함께 버려졌는지 — 거부가 잦은데 이 수가 크면 LLM이
                # 근거 없이 플레이북부터 쓰고 있다는 신호다.
                "dropped_playbook_entries": len(playbook.pending),
            },
        )
        return replace(base, rejected_reasons=exc.reasons)
    except Exception as exc:  # noqa: BLE001 — 한 플랫폼의 사고가 훑기를 끊지 않는다.
        _log(
            store,
            cycle_id=cycle_id,
            reason="analysis_failed",
            sample=sample,
            extra={"error": f"{type(exc).__name__}: {exc}"},
        )
        return replace(base, error=f"{type(exc).__name__}: {exc}")

    # 여기까지 왔다는 것은 검증기를 통과했다는 뜻 — 이제야 지침이 원장으로 간다.
    written = playbook.flush(store)
    note_id = store.save_analysis_note(
        cycle_id=cycle_id, body=result.body, insufficient_evidence=result.insufficient_evidence
    )
    return replace(
        base,
        note_id=note_id,
        insufficient_evidence=result.insufficient_evidence,
        playbook_written=written > 0,
    )


def write_analysis_notes(
    model: BaseChatModel,
    store: MetricStore,
    *,
    platforms: Sequence[Platform] = ("youtube", "instagram"),
    window_index: int = REWARD_WINDOW_INDEX,
    cycle_id: str | None = None,
    baseline_limit: int = DEFAULT_BASELINE_LIMIT,
    since: datetime | None = None,
    ledger_limit: int = DEFAULT_LEDGER_LIMIT,
) -> tuple[NoteReport, ...]:
    """플랫폼마다 1회씩. **원장 스냅샷은 하나**라 둘이 같은 표본 시점을 본다.

    한 플랫폼의 사고가 다른 플랫폼 분석을 막지 않는다(`sns/publish/router.py`와 같은
    규율). `write_analysis_note`가 이미 대부분을 삼키지만, 적재 자체가 터지는 경우까지
    여기서 받아 낸다.
    """
    stored = StoredMetrics(store, since=since, limit=ledger_limit)
    reports: list[NoteReport] = []
    for platform in platforms:
        try:
            reports.append(
                write_analysis_note(
                    model,
                    store,
                    platform=platform,
                    window_index=window_index,
                    cycle_id=cycle_id,
                    baseline_limit=baseline_limit,
                    stored=stored,
                )
            )
        except Exception as exc:  # noqa: BLE001 — 적재 실패 등 마지막 방어선.
            reports.append(
                NoteReport(
                    platform=platform,
                    window_index=window_index,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return tuple(reports)


def _log(
    store: MetricStore,
    *,
    cycle_id: str | None,
    reason: str,
    sample: AnalysisSample,
    extra: dict[str, object],
) -> None:
    store.log_event(
        cycle_id=cycle_id,
        kind="error",
        payload={
            "reason": reason,
            "platform": sample.platform,
            "window_index": sample.window_index,
            "post_id": sample.target_post_id,
            "baseline_count": len(sample.baseline_post_ids),
            **extra,
        },
    )
