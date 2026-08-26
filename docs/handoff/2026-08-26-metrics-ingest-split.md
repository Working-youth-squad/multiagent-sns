# 지표 적재 배선(M6) — 병렬 작업 분할 (2026-08-26)

> **[2026-08-27] 네 트랙 전부 착지했다** — A #46 · B #49 · C #50 · D #48, seam #45.
> 겹치는 파일 0이라는 전제가 실제로 지켜졌다(공유 파일 3종 무변경, `sns/learning/__init__.py` 0바이트 유지).
> 아래 §1은 그대로 두고 읽는다 — **무엇을 왜 그렇게 만들었나**의 기록이다. 지금 할 일은 §4다.

**왜 지금**: 인사이트 탭(FR-W3)은 기획서상 **필수**인데 아직 `sns/web/insights/`가 없다.
그런데 화면을 먼저 만들면 전 구역이 "근거 부족"만 뜬다 — `metric_observation`·
`metric_value`·`reward`·`topic_stats`·`playbook`·`analysis_note` 여섯 테이블은 스키마만
있고 **INSERT가 레포 전체에 0건**이었다. 그래서 화면보다 적재가 먼저다.

**seam은 끝났다**(이 PR): `sns/learning/stores.py` — `MetricStore` 계약 + InMemory/Pg.
아래 네 트랙은 **서로 기다리지 않는다.**

---

## 0. 분할 원칙 — 왜 이 선이 그어졌나

`docs/plan/14-태스크분할.md` §0의 규칙을 그대로 따른다: **seam 먼저 함께, 그다음 태스크 =
파일 묶음 1개**. 여기서 seam은 두 겹이다.

| 겹 | 무엇 | 누가 정하나 |
|---|---|---|
| 저장 | `MetricStore`(`sns/learning/stores.py`) | **동결됨(이 PR)** — 변경은 PR + 상대 리뷰 |
| 읽기 | `StoredMetrics`·`as_metric_map`(`sns/learning/observations.py`) | **동결됨(이 PR)** — B·C 공용 |
| 수집 | `PollMetrics`(`sns/tools/contracts.py`) | 이미 동결(T0-3) |

저장소가 **정책을 담지 않는 것**이 분할의 핵심이다. `published_items()`가 "무엇이 발행됐고
어떤 창이 이미 찍혔나"를 함께 돌려주므로, **A는 DB 없이** 스케줄을 시험하고, **D는 DB를
전혀 모른 채** IG 폴러를 만들고, **B는 관측값만 읽어** 산식을 시험한다.

---

## 1. 네 트랙

### A. 폴링 스케줄 + 폴러 러너 (FR-L1)

| | |
|---|---|
| 소유 파일 | `sns/learning/schedule.py`(신설·순수) · `sns/learning/poller.py`(신설) · `scripts/run_metrics_poll.py`(신설) · `tests/test_metric_schedule.py` · `tests/test_metrics_poller.py` |
| 읽는 계약 | `MetricStore.published_items` / `save_observation` / `log_event`, `PollMetrics` |
| 하는 일 | 창 스케줄(0=6h · 1=24h · 2=72h · 3+=이후 일 1회)을 순수 함수로 계산 → 대상마다 `PollMetrics` 호출 → 관측 적재 → `run_event('metric_polled')` |
| 반드시 지킬 것 | ① **결측을 0으로 채우지 않는다**(어댑터가 준 `missing`을 그대로 통과시킨다) ② 어댑터 예외는 **그 건만** 격리하고 루프를 끊지 않는다(`sns/publish/router.py`가 같은 이유로 만들어졌다) ③ 이미 찍힌 창은 다시 부르지 않는다(쿼터) |
| 결정할 것 | 폴링 지평 — '이후 일 1회'를 언제까지? `published_items(since=)`가 그 손잡이다. **제안: 발행 후 14일**(그 뒤엔 곡선이 평평해 조기 판정에 쓸모가 없다) |
| DoD | 가짜 `PollMetrics`로 결정론 테스트 + 실 DB 통합 1건. 실패 주입 시 다른 건이 계속 폴링되는 테스트 |

> ⚠️ 창 인덱스는 **관측의 이름표지 시각이 아니다**. YT Analytics는 일 단위 granularity에
> 24–48h 지연이라 6h 창을 표현할 수 없다(`sns/adapters/youtube/metrics.py` docstring).
> 6h 창은 "발행 후 6시간 시점에 본 누적치"라는 뜻이다.

### B. RewardFn + 보상 배치 (FR-L2)

| | |
|---|---|
| 소유 파일 | `sns/learning/reward.py`(신설) · `scripts/run_reward_batch.py`(신설) · `tests/test_reward.py` |
| 읽는 계약 | `MetricStore.published_items` / `save_reward`, `sns/learning/observations.py`(관측 읽기), `sns/goals.py`, `sns/signals/scoreboard.py` |
| 하는 일 | 관측창 → `float` 또는 `None`. goal별 가중합(공식 신호 정렬), 조회수는 log 보조, 데이터 부족이면 **None**(학습 제외) |
| 반드시 지킬 것 | ① `save_reward`가 `topic_stats`를 알아서 갱신한다 — **호출부에서 따로 더하지 말 것**(중복 집계) ② `None`과 0.0을 섞지 말 것: 전자는 표본 아님, 후자는 "성과가 0" ③ `formula_version`은 상수로 못박아 사전등록한다 |
| 결정할 것 | **계수**. 기획서가 "M1 실측 후 사전등록"(FR-L2)이라 임의로 정하면 안 된다. **제안: `v0-unweighted`로 뼈대만 넣고 계수는 상수 블록 하나에 모아 두기** — 실측 후 그 블록만 교체 |
| DoD | 결측 조합별 테이블 테스트(전부 결측 → None), 재계산이 `trials`를 안 부풀리는 테스트 |

### C. 분석글·플레이북 착지 (FR-L4·L5)

| | |
|---|---|
| 소유 파일 | `sns/learning/report.py`(신설) · `scripts/run_analysis_note.py`(신설) · `tests/test_analysis_report.py` · `scripts/e2e_analyst.py`(실 store 전환) |
| 읽는 계약 | `MetricStore.read_topic_stats`(=`ReadStats`) / `save_playbook`(=`WritePlaybook`) / `save_analysis_note`, **`StoredMetrics`**(= analyst의 `poll_metrics` 자리), `sns/learning/validator.py`, `sns/signals/scoreboard.py` |
| 하는 일 | 관측 → 스코어보드 JSON → Analyst 에이전트 → **검증기 통과분만** 적재 |
| 반드시 지킬 것 | ⓪ `run_analysis`의 `poll_metrics`에 **실 어댑터를 물리지 말 것** — `StoredMetrics`를 넘긴다(안 그러면 분석마다 API를 다시 때리고, 폴링 시점과 값이 갈린다) ① 검증기 거부는 저장하지 않고 `run_event('error')`로 남긴다 — 지어낸 인용이 원장에 들어가면 그게 다음 사이클의 근거가 된다 ② 수치는 코드가 계산하고 LLM은 서술만(FR-L5) |
| 현황 | `scripts/e2e_analyst.py`가 `FakeReadStats`/`FakeWritePlaybook`으로 돌고 있다("DB 없음 — 러너 연결은 후속"). 그 두 자리에 `PgMetricStore`의 메서드를 그대로 넣으면 된다 |
| DoD | 검증기 거부 시나리오에서 `analysis_note` 0건 + `run_event` 1건 |

### D. IG 인사이트 폴러 (IG-3)

| | |
|---|---|
| 소유 파일 | `sns/adapters/instagram/metrics.py`(신설) · `tests/test_instagram_metrics.py` |
| 읽는 계약 | `PollMetrics`만. **`MetricStore`를 몰라도 된다** |
| 하는 일 | IG Graph insights → `MetricValue` 튜플. metric_key 표준은 `docs/plan/11-데이터모델.md` §4 |
| 반드시 지킬 것 | ① 값이 없으면 `missing=True`(0 금지) ② API '오류'는 raise로 전파 — 결측으로 뭉개면 NULL의 의미가 오염된다(YT 폴러 docstring이 같은 결정을 적어 뒀다) |
| 참고 | 신호 정의는 이미 `sns/signals/scoreboard.py`의 `SIGNAL_DEFS["instagram"]`에 있다(shares/reach 등) — 그 `metric_key`를 그대로 채우면 스코어보드가 바로 돈다 |
| DoD | 가짜 HTTP transport로 결측·오류 분기 테스트. YT 폴러(`tests/test_youtube_metrics.py`)가 본보기 |

---

## 2. 충돌 지도

```
sns/learning/stores.py        ← 동결(이 PR). 넷 다 읽기만
sns/learning/observations.py  ← 동결(이 PR). B·C가 읽기만 (관측 → 지표)
   ├── A  schedule.py · poller.py · scripts/run_metrics_poll.py
   ├── B  reward.py           · scripts/run_reward_batch.py
   ├── C  report.py           · scripts/run_analysis_note.py
   └── D  adapters/instagram/metrics.py     (계약만 의존 — DB 무관)
```

**겹치는 파일이 없다.** 주의할 자리는 셋뿐이다:

| 자리 | 규칙 |
|---|---|
| `sns/learning/__init__.py` | 지금 **0바이트**다. 각자 export를 추가하면 그 한 줄이 유일한 충돌점이 된다 — **아무도 건드리지 않는 편이 낫다**(`from sns.learning.reward import ...` 전체 경로로 import) |
| `scripts/` CLI | **트랙마다 파일 하나**. 한 파일에 서브커맨드로 합치면 넷이 같은 줄을 고친다 |
| `MetricStore` 계약 | 부족한 것이 생기면 **먼저 PR로 계약만** 올리고 리뷰받는다(14-태스크분할 §0-1) |

## 3. 의존과 순서

```
A(폴러) ──관측 적재──▶ B(reward) ──topic_stats──▶ C(분석글·플레이북)
D(IG 폴러) ──PollMetrics 구현──▶ A가 그대로 물림
```

의존은 **데이터**지 **코드**가 아니다. B는 A가 없어도 `InMemoryMetricStore`에 관측을
손으로 넣고 산식을 완성할 수 있고, C는 B가 없어도 `save_reward`로 통계를 만들어 놓고
붙일 수 있다. **넷 다 지금 시작할 수 있다.**

실제 데이터가 필요한 시점은 하나뿐이다 — **실 계정 발행분이 쌓인 뒤**. 그때까지는
가짜 어댑터로 관통을 끝내 둔다.

## 4. 그다음 — 인사이트 탭 (FR-W3, C10)

**A~D가 끝났으니 이제 화면 차례다.** 화면은 기존 3앱과 같은 규율(폼 POST · JS 0줄 ·
템플릿 엔진 없음)이고, 4구역은 `docs/plan/10-웹-알림.md` §2에 있다. 수치는 코드 집계,
서술은 `sns/learning/validator.py`를 통과한 것만.

읽을 자리는 이미 다 있다:

| 구역 | 읽는 곳 |
|---|---|
| ① 계정 카드·추이 | `MetricStore.published_items` + `read_observation`(창별 시계열) |
| ② auto vs hybrid | `PublishedItem.channel_mode` — 발행 건마다 따라온다 |
| ③ 신호 스코어보드 | `sns/signals/scoreboard.py` + `StoredMetrics`(네트워크 안 탐) |
| ④ 주간 분석글 | `analysis_note` — `run_analysis_note.py`가 적재한 것 |

**⚠️ 다만 원장에 관측이 아직 0건이다.** 화면을 먼저 만들면 전 구역이 "판정 불가"로 뜬다 —
그것이 정상 동작이지만, 화면이 옳은지는 알 수 없다. 실 계정 발행분이 쌓이기 전에 만들려면
가짜 관측을 시드하는 스크립트가 함께 있어야 한다.

## 5. 정해진 것 / 미결정 (팀)

**정해짐(2026-08-26)**

- **reward 대표 창 = 72h(`REWARD_WINDOW_INDEX = 2`)**. 초기 확산이 대체로 끝난 시점이고,
  6·24h는 조기 판정(FR-A3) 전용으로 남긴다. B와 C가 **같은 창**을 봐야 보상과 리포트가
  같은 사실을 말한다 — 각자 정하면 갈린다.
- **관측 읽기는 `sns/learning/observations.py` 하나**(§0). B·C가 각자 만들지 않는다.

**미결정**

1. **reward 계수 사전등록** — B의 블로커가 아니라 B의 마지막 한 줄이다(§1-B).
2. **폴링 지평** — 제안 14일(§1-A).
3. **`content_item.topic_category` 컬럼이 없다** — 밴딧 arm 축 하나가 통째로 빠진다
   ([15](../plan/15-구현-이탈기록.md) §3.2). 그 문서가 "지표 적재와 같은 작업으로 묶는 게 맞다"고
   적어 뒀는데, 그 지표 적재가 이제 끝났다 — **지금이 그 자리다.**
4. **hybrid 수동 발행분의 원장 등록** — `docs/plan/10-웹-알림.md` §4.4. 손으로 올린 건은
   `publication`이 `pending`으로 남아 **폴링 대상에서 빠진다**(`published_items`는
   `status='published'`만 본다). 인사이트에 구멍이 생기는 자리라 A 착수 전에 정하는 편이
   좋다 — 후보 3안은 그 문서에.
