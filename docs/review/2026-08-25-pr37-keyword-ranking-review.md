# PR #37 코드리뷰 결과 — 질의어 기반 키워드 랭킹 (2026-08-25)

**대상**: PR #37 `feat/trend-keyword-stats` → `main` · **리뷰 시점 HEAD**: `4e49608`
**처리 방침**: **이 PR에서는 고치지 않는다.** 머지 후 후속 브랜치에서 일괄 수정한다.
이 문서는 그때 무엇을 고칠지에 대한 단일 출처다.

> 통계 코어(`pct_rank_of` · `percentile` · 관측치만으로 `pstdev` · `id()` 기반 `dropped`
> 필터링 · `MIN_BAND_POOL`/`MIN_BAND_SOURCES` 게이트)는 리뷰에서 문제를 찾지 못했다.
> PR 본문이 실 API 관통에서 잡았다고 적은 두 결함(UA 없을 때 구글이 EUC-KR로 응답,
> `if s.rank_std` 진리값 컷이 하위 꼬리 0.0을 삼킴)도 올바르게 고쳐져 있고 회귀 테스트로
> 고정돼 있다. 아래 8건은 **그 바깥**의 것이다.

---

## 요약

| # | 등급 | 위치 | 한 줄 |
|---|---|---|---|
| [R1](#r1) | medium | `sns/research/sources/suggest.py:26` | 비-`str` 원소를 `str()`로 문자열화 → 깨진 키워드가 LLM까지 흘러감 |
| [R2](#r2) | medium | `sns/research/ranking.py:202` | 제외 키워드 판정이 공백 변종을 못 잡고, 소스 순서에 따라 결과가 뒤집힘 |
| [R3](#r3) | medium | `sns/research/keywords.py:180` | `service=` 주입이 무력화 — 하드코딩 3소스가 강제됨 |
| [R4](#r4) | low | `sns/research/ranking.py:151` | 중복 소스명이 두 번 계산 → 관측 1건이 `present_count=2`·`rank_std=0.0` |
| [R5](#r5) | low | `sns/research/sources/suggest.py:28` | 자기제외에 **에코된** 질의어를 씀 → 질의어 자신이 후보로 남을 수 있음 |
| [R6](#r6) | low | `scripts/rank_keywords.py:114` | 공백 질의어가 트레이스백 + exit 1 (문서가 약속한 exit 2 아님) |
| [R7](#r7) | low | `sns/research/keywords.py:220` | `ranking_to_dict` 계약 구멍 — `pool` 누락 · `unscored` 이중 집계 |
| [R8](#r8) | low | `scripts/rank_keywords.py:54` | `--no-band` 힌트가 밴드를 안 쓴 경우에도 무조건 출력 |

**연동 팀 영향**: R2·R7은 핸드오프 문서(`docs/handoff/2026-08-25-keyword-ranking-handoff.md`)가
챗봇/LLM 팀에게 약속한 계약(§2 토글 ④, §1.2 JSON)에 직접 걸린다. 수정 전까지는 이 문서를
함께 읽어야 한다.

---

<a id="r1"></a>
## R1 (medium) — 비-`str` 원소를 그대로 문자열화한다

**위치**: `sns/research/sources/suggest.py:26` (`parse_suggest`) · `sns/research/sources/_autocomplete.py:26` (`related_terms`)

`parse_suggest`는 `payload[1]`이 리스트인지**만** 검사하고 원소 타입은 보지 않는다.
그 뒤 `related_terms`가 각 원소에 `str(candidate)`를 건다.

```python
# _autocomplete.py:26
text = str(candidate).strip()
```

`suggest.py` 자체 docstring이 "비공식 엔드포인트라 예고 없이 형식·정책이 바뀔 수 있다"고
적어 두었는데, 정작 그 변화를 잡지 못한다. `client=chrome`이나 다른 `ds` 변종은 연관어를
배열로 돌려주는데, 그 경우 `ValueError`가 나서 서비스가 소스를 격리하는 대신 **문자열화된
파이썬 리터럴이 키워드로 통과**한다.

```
입력: ["등산", [["등산화", 0, [1]], ["등산복", 0]], []]
출력: ("['등산화', 0, [1]]", "['등산복', 0]")
```

이 값은 등수 통계를 거쳐 챗봇 응답과 LLM 프롬프트까지 그대로 간다. 소스 격리(FR-G4)가
설계상 막아 주기로 한 지점인데, 예외가 발생하지 않으니 격리가 작동할 기회 자체가 없다.

**대조**: `parse_naver_autocomplete`는 같은 문제를 `isinstance(entry[0], str)`로 이미 막고
있다(`naver_autocomplete.py:31`). 두 파서의 방어 수준이 다르다.

**수정 방향**

1. `related_terms`에서 `str()` 강제를 걷어내고 비-`str` 원소는 건너뛴다(네이버 파서와 같은 규율).
2. `parse_suggest`에서 리스트가 비어 있지 않은데 `str` 원소가 하나도 없으면 `ValueError`를
   던진다 — 형식이 통째로 바뀐 것은 "연관어 0건"이 아니라 **소스 실패**로 보고돼야 한다.

---

<a id="r2"></a>
## R2 (medium) — 제외 키워드가 공백 변종을 놓치고, 소스 순서에 의존한다

**위치**: `sns/research/ranking.py:202` (`excluded_by`) · `sns/research/ranking.py:127` (`_ranked`)

두 정규화가 서로 다른 층에서 쓰인다.

| 층 | 함수 | 정규화 | 결과 |
|---|---|---|---|
| 후보 병합 | `_ranked` / `KeywordStat.key` | `squeezed` (공백 **제거**) | "리콜대상"과 "리콜 대상"이 **한 후보로 합쳐짐** |
| 제외 판정 | `excluded_by` | `collapsed` (공백 **유지**) | 적어 놓은 띄어쓰기로만 매칭됨 |

`keytext.py`가 두 함수를 나란히 두고 "섞어 쓰면 조용히 오탐이 난다"고 경고한 그 지점이다.
`collapsed`를 쓰는 이유 자체는 타당하다("최고 장점" → "최고장점" ⊃ "고장" 오탐 방지).
문제는 **병합된 뒤의 대표 표기 하나**에만 그 검사를 건다는 것이다.

살아남는 `stat.text`는 `display.setdefault`가 정하는데(`ranking.py:158-161`), 이는
**요청한 소스 순서상 처음 등장한 표기**다. 그래서 같은 입력 집합이라도 소스 순서에 따라
제외 여부가 뒤집힌다.

```
exclude = ["리콜 대상"]
소스 A = ("리콜대상", ...) · 소스 B = ("리콜 대상", ...)

B가 먼저 나열되면  → stat.text = "리콜 대상" → 제외됨
A가 먼저 나열되면  → stat.text = "리콜대상"  → 그냥 통과
```

이 자리는 도메인이 채우는 **부정 키워드 필터**다(핸드오프 §4, 토글 ④). 브랜드 리스크
단어가 붙여쓴 형태로 조용히 통과하는데, 한국어 자동완성에서 붙여쓴 형태는 매우 흔하다.

**수정 방향**

1. **(버그)** 후보가 소스들에서 실제로 관측된 **모든 표기 변종**을 `KeywordStat`에 남기고,
   `excluded_by`를 그 전부에 대해 돌린다. 이것만으로 소스 순서 의존은 사라진다.
2. **(잔존 한계 — 정책 결정)** 어느 소스도 띄어쓴 형태를 내지 않았는데 `exclude`에는
   띄어쓴 형태만 있는 경우는 1번으로도 안 잡힌다. 한국어에는 단어 경계가 없어
   공백 무시 부분일치는 "고장" 류 오탐과 맞바꾸는 선택이다. 셋 중 하나를 골라야 한다:
   - `exclude` 목록에 띄어쓰기 변종을 함께 넣도록 **핸드오프에 명시**(비용 0, 호출자 책임)
   - 공백 무시 매칭을 **별도 토글**로 열어 두고 오탐 위험을 문서화
   - 현행 유지 + 한계를 문서화

---

<a id="r3"></a>
## R3 (medium) — `service=` 주입이 소스 선택을 무력화한다

**위치**: `sns/research/keywords.py:180`

```python
active = service or keyword_service(query, timeout_s=timeout_s)
selected = tuple(sources) if sources is not None else KEYWORD_SOURCES   # ← 여기
digest = active(selected, limit=limit)
```

`sources`가 `None`이면 **모듈 상수 3종**을 강제한다. 그런데 `ResearchTrendsService.__call__`은
`sources is None`일 때 자기 레지스트리로 폴백하도록 이미 돼 있다(`trends.py:47`).

```python
selected = sources if sources is not None else tuple(self._fetchers)
```

`rank_keywords`의 docstring은 `service`를 "테스트가 네트워크 없이 도는 지점"이라고
광고하는데, 3종 이외의 이름으로 소스를 등록한 서비스를 주입하면 전부 미등록으로
격리된다.

```
소스 "mine" 하나를 등록한 서비스 주입 시:
  sources_ok=()
  sources_failed=('naver_autocomplete', 'google_suggest', 'youtube_suggest')
  후보 0건 → CLI는 exit 1
```

현재 테스트가 이걸 못 잡는 이유는 주입하는 가짜 서비스가 전부 같은 3개 이름을 쓰기 때문이다.

**수정 방향**: `sources`를 `None` 그대로 넘기고 폴백을 서비스에 맡긴다. `KEYWORD_SOURCES`는
CLI `choices`와 문서용 상수로만 남긴다. 임의 이름 레지스트리를 주입하는 회귀 테스트를 하나 추가.

---

<a id="r4"></a>
## R4 (low) — 중복 소스명이 두 번 계산된다

**위치**: `sns/research/ranking.py:151`

```python
live = [r for r in results if r.ok]                        # 리스트 — 중복이 남는다
per_source = {r.source: _ranked(r.items) for r in live}    # dict — 중복이 접힌다
```

두 자료구조의 중복 처리가 다르다. `results`에 같은 `source` 이름이 두 번 들어오면
`per_source`는 하나로 접히지만 `live` 순회는 두 번 돌아, 후보마다 `SourceRank`가 두 개
붙는다. 결과적으로 **소스 하나가 본 키워드**가 이렇게 나온다.

```
present_count = 2       ← 교차검증된 것처럼 보임 (정렬 1순위!)
rank_std      = 0.0     ← "완벽 일치"
```

이 PR이 `rank_std: float | None`로 막으려던 바로 그 혼동이다 — 관측 1건인데 `None`이
아니라 `0.0`을 받고, 밴드 판정 대상이 되며, 정렬 최상위로 올라간다.

**도달 경로**: 문서화된 CLI에서 그대로 된다. `--source`가 `action="append"`이고
`choices`는 중복을 막지 않는다(`scripts/rank_keywords.py:81`).

```bash
uv run python scripts/rank_keywords.py 등산 --source google_suggest --source google_suggest
```

덤으로 `ResearchTrendsService.__call__`은 중복 소스에 대해 `executor.submit`을 두 번
호출한다(`trends.py:54` — dict 컴프리헨션이라 future 하나만 남지만 요청은 두 번 나간다).
같은 엔드포인트를 불필요하게 두 번 때린다.

**수정 방향**: `__call__`에서 `selected`를 **순서 보존 중복 제거**한다(`dict.fromkeys`).
`trends.py`는 `contracts.py`와 달리 동결 대상이 아니고, 기존 호출자에게도 무해한 개선이다.
추가로 `rank_stats`가 중복 소스명을 받으면 방어하도록 한 줄 더 두는 것을 권한다 —
공개 함수라 다른 경로로도 들어올 수 있다.

---

<a id="r5"></a>
## R5 (low) — 자기제외에 에코된 질의어를 쓴다

**위치**: `sns/research/sources/suggest.py:28`

```python
query = payload[0] if isinstance(payload[0], str) else ""
return related_terms(query, payload[1])
```

실제로 요청한 질의어가 아니라 **응답이 되돌려준 값**으로 자기 자신을 뺀다. 에코가 없거나
문자열이 아니면 `query=""`가 되고, `related_terms`의 `squeezed("") == ""`는 어떤 후보와도
같지 않으므로 **질의어 자신이 연관어로 남는다.**

```
입력: [["개발자"], ["개발자", "개발자 연봉"]]      # payload[0]이 리스트
출력: ('개발자', '개발자 연봉')                     # 질의어가 그대로 1위
```

`_autocomplete.py` docstring이 "완전 일치로만 빼면 그 변형이 등수 한 칸을 차지해 소스별
백분위(ranking)가 흔들린다"고 경고한 실패가 그대로 난다. 질의어가 1위 슬롯을 먹으면
그 소스의 모든 후보 백분위가 한 칸씩 밀린다.

**대조**: `parse_naver_autocomplete`는 `query=`를 인자로 받아 요청값을 쓴다
(`naver_autocomplete.py:16, 49`). `suggest.py`의 `_fetch`도 `query`를 인자로 들고 있으므로
넘기기만 하면 된다.

**수정 방향**: `parse_suggest(data, *, query: str = "")`로 바꾸고 `_fetch`가 자기 `query`를
넘긴다. 에코는 무시한다.

---

<a id="r6"></a>
## R6 (low) — 공백 질의어가 트레이스백으로 죽는다

**위치**: `scripts/rank_keywords.py:114`

`query` positional에는 검증이 없다. 공백만 준 질의어가 `rank_keywords` →
`keyword_service`까지 내려가 `ValueError("질의어가 비어 있다")`를 던지는데
(`keywords.py:68`), CLI가 잡지 않는다.

```
$ uv run python scripts/rank_keywords.py "   "
Traceback (most recent call last): ...
ValueError: 질의어가 비어 있다     → exit 1
```

모듈 docstring은 "2 인자 사용 오류(argparse 관례)"를 약속한다(`rank_keywords.py:17`).
exit 1은 "전 소스 실패"에 배정된 코드라, 자동화가 인자 오류를 네트워크 장애로 오독한다.

**수정 방향**: `query`에 `type=`(공백 거부) 또는 `parse_args` 직후 `parser.error`.
`--band` 뒤집힘을 `parser.error`로 처리한 방식(`rank_keywords.py:111-112`)과 같은 결.

---

<a id="r7"></a>
## R7 (low) — `ranking_to_dict`의 계약에 구멍이 있다

**위치**: `sns/research/keywords.py:193-222`

프로세스 경계(챗봇이 다른 언어일 때)가 쓰는 유일한 모양인데 세 가지가 빠져 있다.

**① `pool`이 아예 없다.** `KeywordRanking`은 `pool`(컷 이전 전량)을 두고 dataclass
docstring이 그 이유를 "왜 이 키워드가 빠졌는지 되짚을 수 있어야 하기 때문"이라고 적었다
(`ranking.py:86-87`). JSON에는 없다. `top` 컷으로 잘린 후보는 JSON 소비자에게 **흔적이 없다.**

**② `unscored`가 이중 집계된다.** `unscored` 항목은 `candidates` 안에도 들어 있다.
dataclass 필드 docstring에는 그 사실이 적혀 있지만(`ranking.py:100`) JSON에는 없다.

```
소스 두 개 ("x","y") / ("x","z")
→ candidates = ['x','y','z'],  unscored = ['y','z']
   len(candidates) + len(dropped) + len(unscored) = 5  ← 실제 후보는 3개
```

**③ `min_present`로 걸러진 후보는 어느 필드에도 안 남는다**(`keywords.py:111-112`).
`excluded`처럼 사유가 기록되는 다른 필터와 대칭이 깨진다.

**수정 방향**

- `pool`을 JSON에 추가한다.
- 각 stat 항목에 `"scored": bool`(또는 `"unscored": bool`)을 넣어 관계를 자명하게 만든다.
  최상위 `unscored` 목록은 호환을 위해 남기되, 무엇의 부분집합인지 문서에 적는다.
- `min_present` 컷을 `dropped`와 구분되는 필드나 `reason`에 남긴다.

---

<a id="r8"></a>
## R8 (low) — `--no-band` 힌트가 무조건 출력된다

**위치**: `scripts/rank_keywords.py:53-54`

```python
if not ranking.candidates:
    lines.append("    (후보 없음 — --no-band 로 전량을 확인해 볼 것)")
```

후보가 0건이기만 하면 `filter_mode`와 무관하게 찍힌다. `off`(이미 `--no-band`를 준 경우)와
`passthrough`(표본 부족으로 밴드가 아예 안 열린 경우)에서는 **`--no-band`가 도움이 될 수
없다.** 밴드가 자른 게 아니라 소스가 아무것도 못 준 상황인데, 필터 탓으로 오인하게 만든다.

`filter_mode` 3값을 굳이 구분해 기록한 이 PR의 취지(뭉뚱그리면 "필터 없는 척")와 어긋난다.

**수정 방향**: `filter_mode == "active"`일 때만 힌트를 낸다. 나머지 두 경우는 소스 응답이
비었다는 쪽으로 안내한다.

---

## 리뷰에서 문제를 찾지 못한 부분

- `pct_rank_of` — `length+1` 분모로 결측 1.0과 관측 꼴찌를 분리하는 규율, 음수 길이 거부
- `percentile` — 선형 보간, 단일 원소·경계값 처리
- `rank_stats` — 실패 소스 제외 / **빈 관측은 참여**의 구분, 관측치만으로 `pstdev`,
  관측 1건 → `None`, `(-present_count, observed_mean, text)` 결정론 정렬
- `aggregate` — `id()` 기반 `dropped` 필터링(동일 내용 후보를 값 비교로 지우지 않음),
  `MIN_BAND_POOL`/`MIN_BAND_SOURCES` 게이트와 `filter_mode` 3값 기록
- `keytext` — `squeezed`/`collapsed` 분리 자체는 옳다(R2는 적용 층의 문제지 이 함수의 문제가 아님)
- 실 API 관통에서 잡았다는 두 결함(구글 EUC-KR, `if s.rank_std` 진리값 컷) — 수정·회귀 테스트 확인
- 테스트가 네트워크를 접촉하지 않는 규율(`service=`/`opener=` 주입) 준수

## 별건 메모 — 버그 아님

`USER_AGENT` 리터럴이 이제 세 번째 복사본이다.

| 파일 | 줄 |
|---|---|
| `sns/research/sources/_autocomplete.py` | 20 |
| `sns/research/sources/devnews.py` | 27 |
| `sns/render/images/pexels.py` | 30 |

공용 opener가 있는 `sns/net/http.py`로 올릴 자리다. UA가 응답 인코딩을 가르는 소스가
생긴 이상(`_autocomplete.py:41-48`), 값이 갈라지면 조용히 깨진다.

---

## 후속 수정 제안 순서

머지 후 한 브랜치에서 처리하되, 성격이 달라 커밋은 나누는 편이 낫다.

1. **파서 방어** — R1 · R5 (`sources/` 안에서 끝남, 회귀 테스트 추가)
2. **주입·중복** — R3 · R4 (`keywords.py` · `trends.py`, 테스트 규율에 직접 걸림)
3. **제외 판정** — R2 (표기 변종 보존 = `KeywordStat` 필드 추가 → 잔존 한계는 정책 결정 필요)
4. **경계 계약** — R7 (JSON 모양 변경 → 핸드오프 §1.2 동시 갱신)
5. **CLI 마감** — R6 · R8
6. **정리** — `USER_AGENT` 단일화

3번의 정책 선택(R2 잔존 한계)은 팀 판단이 필요하다. 나머지는 판단 없이 진행 가능하다.
