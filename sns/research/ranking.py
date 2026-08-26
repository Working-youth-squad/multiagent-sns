"""3소스 등수 통계 + 표준편차 밴드 — 질의어 하나를 근거 있는 키워드 목록으로.

`ResearchTrendsService`가 돌려주는 소스별 순서열을 받아, 소스마다 길이가 달라도 비교할 수
있게 **백분위 등수**로 바꾸고, 후보별로 세 값을 낸다:

| 값 | 뜻 | 방향 |
|---|---|---|
| `present_count` | 몇 개 소스가 이 키워드를 내놨나 | 높을수록 교차검증됨 (정렬 1순위) |
| `observed_mean` | **관측된 소스에서만** 낸 평균 등수 | 낮을수록 인기 (정렬 2순위) |
| `rank_std` | **관측된 소스들 사이의** 등수 불일치 | `None` = 관측 1건이라 정의 불가 |

네 가지 결정이 여기 박혀 있고, 셋은 실측에서 배운 것이다.

1. **소스 원값은 흐르지 않는다.** 데이터랩 ratio·조회수는 단위가 달라 그대로 합치면 단위
   차이만 측정한다. 입력이 처음부터 문자열 순서열뿐이라 값을 넣을 자리 자체가 없다.
2. **백분위 등수의 분모는 길이+1이다.** "결측 = 최하위+1"을 리스트 길이로 나누면
   `(L+1)/L > 1.0`이 되어 등수가 1.0을 넘는다. +1 분모는 결측을 정확히 1.0으로 보내고
   관측된 항목은 전부 1.0 미만에 둔다 — "관측된 꼴찌"와 "아예 없었다"가 같은 값이 되지 않는다.
3. **분산은 관측된 등수끼리만 잰다. 관측이 1건이면 `None`이다.** 결측 페널티 1.0은
   관측값이 아니라 대입값이다. 분산에 넣으면 분산이 "불일치"가 아니라 "결측이 몇 개인가"를
   재게 된다. 실측(2026-08-25)에서 이게 그대로 터졌다 — 2개 소스가 아는 키워드와 한 소스만
   아는 밈 문구가 **같은 표준편차**를 받았고, 단일 소스 구간에서는
   `rank_std = √2 × (1 − 평균등수)`가 되어 평균 등수와 상관계수 −1, 즉 새 정보가 0이었다.
   `0.0`이 아니라 `None`인 이유: "불일치가 없다"와 "불일치를 잴 수 없다"는 다른 사실이다.
4. **정렬을 대입 상수에 맡기지 않는다.** 결측을 1.0으로 대입한 평균(Borda)으로 정렬하면
   그 상수가 "몇 소스가 봤나"와 "얼마나 높았나"의 교환비를 혼자 정한다. 1.0에는 근거가 없다.
   그래서 `present_count` → `observed_mean` 두 직접 측정값으로 정렬한다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean, pstdev
from typing import Literal

from sns.research.keytext import collapsed, squeezed
from sns.tools.contracts import SourceResult

MISSING_PCT_RANK = 1.0
"""결측(그 소스 랭킹에 없었다) 페널티 — '최하위+1'을 정규화한 값."""

BAND_PERCENTILES = (25.0, 75.0)
"""밴드 기본 경계 — 후보 풀 안에서의 상대 위치로 자른다(절대 임계값 아님)."""

MIN_BAND_POOL = 4
"""밴드를 열 수 있는 최소 표본. rank_std가 **정의된** 후보 기준.

3개에 25/75 퍼센타일을 씌우면 사실상 양끝 원소를 집는 제비뽑기가 된다.
"""

MIN_BAND_SOURCES = 2
"""참여 소스가 이보다 적으면 어떤 후보도 rank_std를 가질 수 없다."""

FilterMode = Literal["active", "passthrough", "off"]
"""active=밴드 적용 / passthrough=표본 부족 / off=호출자가 끔. 셋을 뭉뚱그리면 '필터 없는 척'."""


@dataclass(frozen=True)
class SourceRank:
    """후보 1개가 소스 1개에서 받은 백분위 등수. present=False면 결측 페널티(=1.0)."""

    source: str
    pct_rank: float
    present: bool


@dataclass(frozen=True)
class KeywordStat:
    """후보 1개의 등수 통계. 필드 뜻은 모듈 docstring의 표 참조."""

    text: str
    present_count: int
    observed_mean: float
    rank_std: float | None
    per_source: tuple[SourceRank, ...]
    variants: tuple[str, ...] = ()
    """소스들에서 실제로 관측된 **모든 표기 변종**(등장 순). `text`는 그 첫 번째다.

    공백만 다른 표기("리콜대상"/"리콜 대상")는 한 후보로 병합되는데, 병합 뒤 대표 표기
    하나에만 제외 판정을 걸면 **어느 소스가 먼저 나열되느냐에 따라 제외 여부가 뒤집힌다**.
    변종을 전부 남겨 판정이 소스 순서에 의존하지 않게 한다.
    """

    @property
    def key(self) -> str:
        """소스 간 조인·중복 판정에 쓰는 비교용 표기."""
        return squeezed(self.text)

    @property
    def surface_forms(self) -> tuple[str, ...]:
        """관측된 표기 전부. 변종 기록이 없는 옛 객체는 대표 표기 하나로 본다."""
        return self.variants or (self.text,)


@dataclass(frozen=True)
class KeywordRanking:
    """`rank_keywords` 1회 결과 — 챗봇/LLM 프롬프트에 그대로 넘길 수 있는 완결된 응답.

    `candidates`는 밴드 통과 + 정렬 + 상한 컷을 마친 최종 목록이고, `pool`은 컷 이전
    전량이다. 둘을 함께 두는 이유는 "왜 이 키워드가 빠졌는지"를 되짚을 수 있어야 하기 때문.
    """

    query: str
    filter_mode: FilterMode
    band: tuple[float, float] | None
    reason: str
    sources_ok: tuple[str, ...]
    sources_failed: tuple[str, ...]
    candidates: tuple[KeywordStat, ...]
    pool: tuple[KeywordStat, ...]
    dropped: tuple[KeywordStat, ...]
    unscored: tuple[KeywordStat, ...] = ()
    """rank_std가 정의되지 않아 밴드 판정을 받지 않은 후보 — candidates에도 포함된다."""
    excluded: tuple[tuple[str, str], ...] = ()
    """(원문, 걸린 제외 키워드). 호출자가 `exclude`를 준 경우만."""


def pct_rank_of(index: int, length: int) -> float:
    """0-based 순위 → 백분위 등수. 분모가 length+1인 이유는 모듈 docstring 2번."""
    if length < 0:
        raise ValueError(f"리스트 길이는 음수일 수 없다: {length}")
    return (index + 1) / (length + 1)


def percentile(values: Sequence[float], p: float) -> float:
    """정렬 여부와 무관하게 p(0~100) 퍼센타일. 선형 보간(numpy 기본과 같은 정의)."""
    if not values:
        raise ValueError("빈 표본의 퍼센타일은 정의되지 않는다")
    if not 0.0 <= p <= 100.0:
        raise ValueError(f"퍼센타일은 0~100 이어야 한다: {p}")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (p / 100.0)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _ranked(items: Sequence[str]) -> dict[str, tuple[str, float]]:
    """소스 1개의 순서열 → {비교키: (표시 문자열, 백분위 등수)}.

    공백만 다른 변형("개발자 연봉"/"개발자연봉")은 첫 등장 등수로 접는다 — 두 번 세면
    그 소스에서 한 후보가 등수를 두 칸 먹는다.
    """
    out: dict[str, tuple[str, float]] = {}
    for index, text in enumerate(items):
        key = squeezed(text)
        if not key or key in out:
            continue
        out[key] = (text, pct_rank_of(index, len(items)))
    return out


def live_results(results: Sequence[SourceResult]) -> tuple[SourceResult, ...]:
    """통계에 참여하는 소스 결과 — 성공분만, 소스명 중복은 첫 건만 남긴다.

    `rank_stats`와 `aggregate`가 **같은 집합**을 봐야 한다. 한쪽이 리스트로 순회하고
    다른 쪽이 dict로 접으면, 중복 소스명 하나가 후보에 `SourceRank` 두 개를 붙여
    관측 1건짜리 키워드가 `present_count=2`·`rank_std=0.0`을 받는다 — 이 모듈이
    `rank_std: float | None`로 막으려던 바로 그 혼동이 정렬 1순위에서 일어난다.

    서비스(`ResearchTrendsService.__call__`)가 이미 중복을 접지만, 이 함수들은 공개
    API라 다른 경로로도 결과가 들어올 수 있다.
    """
    seen: set[str] = set()
    out: list[SourceResult] = []
    for result in results:
        if result.ok and result.source not in seen:
            seen.add(result.source)
            out.append(result)
    return tuple(out)


def rank_stats(results: Sequence[SourceResult]) -> tuple[KeywordStat, ...]:
    """소스별 수집 결과 → 후보별 등수 통계. 실패 소스(ok=False)는 통계에서 제외한다.

    실패 소스를 결측 1.0으로 채우면 "관측했는데 없었다"와 "관측 자체를 못 했다"가 섞여
    통계가 장애의 함수가 된다(FR-G4 소스 격리의 취지에 반한다). 반면 **빈 관측**
    (ok=True·items=())은 관측 결과이므로 참여시킨다.

    반환은 present_count 내림 → observed_mean 오름 → 표기 순(결정론).
    """
    live = live_results(results)
    if not live:
        return ()

    per_source = {r.source: _ranked(r.items) for r in live}

    # 관측된 표기를 전부 등장 순으로 모은다. 대표 표기(text)는 그 첫 번째 —
    # 요청한 소스 순서상 처음 등장한 표기다(결정론).
    variants: dict[str, list[str]] = {}
    for result in live:
        for key, (text, _) in per_source[result.source].items():
            forms = variants.setdefault(key, [])
            if text not in forms:
                forms.append(text)

    stats: list[KeywordStat] = []
    for key, forms in variants.items():
        text = forms[0]
        ranks: list[SourceRank] = []
        for result in live:
            hit = per_source[result.source].get(key)
            if hit is None:
                ranks.append(SourceRank(result.source, MISSING_PCT_RANK, present=False))
            else:
                ranks.append(SourceRank(result.source, hit[1], present=True))
        observed = [r.pct_rank for r in ranks if r.present]
        stats.append(
            KeywordStat(
                text=text,
                present_count=len(observed),
                observed_mean=fmean(observed) if observed else MISSING_PCT_RANK,
                # 대입값은 분산에 넣지 않는다(모듈 docstring 3번).
                rank_std=pstdev(observed) if len(observed) > 1 else None,
                per_source=tuple(ranks),
                variants=tuple(forms),
            )
        )

    stats.sort(key=lambda s: (-s.present_count, s.observed_mean, s.text))
    return tuple(stats)


def band_bounds(values: Sequence[float], percentiles: tuple[float, float]) -> tuple[float, float]:
    """(하한, 상한) rank_std 경계. 경계값이 뒤집혀 들어오면 거부한다."""
    low_p, high_p = percentiles
    if low_p > high_p:
        raise ValueError(f"밴드 경계가 뒤집혔다: {percentiles}")
    return percentile(values, low_p), percentile(values, high_p)


def excluded_by(text: str, keywords: Sequence[str], *, ignore_spaces: bool = False) -> str | None:
    """text에 걸린 첫 제외 키워드. 없으면 None.

    기본은 대소문자·공백 **양**은 무시하되 공백의 **존재**는 존중한다
    (`keytext.collapsed`) — "리콜  대상"은 걸리고 "최고 장점"은 걸리지 않는다.
    공백을 지우면 '고장'이 생겨 오탐이다.

    `ignore_spaces=True`면 공백을 아예 지우고(`keytext.squeezed`) 부분 일치를 본다.
    한국어에는 단어 경계가 없어, 이 선택은 **"리콜 대상"으로 "리콜대상"을 잡는 것**과
    **"최고 장점"에서 "고장"을 오탐하는 것**을 맞바꾼다. 도메인이 부정 키워드를 좁게
    통제할 수 있을 때만 켠다(핸드오프 §2.1).
    """
    normalize = squeezed if ignore_spaces else collapsed
    haystack = normalize(text)
    for keyword in keywords:
        needle = normalize(keyword)
        if needle and needle in haystack:
            return keyword
    return None


def excluded_match(
    stat: KeywordStat, keywords: Sequence[str], *, ignore_spaces: bool = False
) -> tuple[str, str] | None:
    """후보가 제외에 걸리면 `(걸린 표기, 제외 키워드)`. 안 걸리면 None.

    **관측된 표기 전부**를 본다 — 대표 표기 하나만 보면 소스 나열 순서가 결과를 가른다.
    """
    for form in stat.surface_forms:
        hit = excluded_by(form, keywords, ignore_spaces=ignore_spaces)
        if hit is not None:
            return form, hit
    return None
