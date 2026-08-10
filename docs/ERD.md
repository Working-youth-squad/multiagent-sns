# 데이터 모델 (ERD 초안) — multiagent-sns

> 상태: **초안 (구현 착수 전 리뷰 대상)** · DB: PostgreSQL 단일 인스턴스 · 작성 2026-08-10
> 다이어그램: [`diagrams/erd.mermaid`](diagrams/erd.mermaid)
>
> 설계 원칙: **정규화 + CHECK 물리 강제 + 지표 결측=NULL + append-only 이벤트**.
> 모든 코드 자산은 이 레포에서 **처음부터 신규 구현**한다(외부 코드/과거 자산 재사용 없음).

## 1. 테이블 목록 (13)

| 테이블 | 역할 | 핵심 불변식(CHECK) |
|---|---|---|
| `channel` | 발행 채널(IG/YouTube) + 암호화 토큰 | platform·status enum, 토큰 평문 저장 금지 |
| `cycle` | 발행 사이클(계획 단위, 단일 트랙) | status enum |
| `topic` | 개발자 주제 풀 | status enum |
| `topic_stats` | 주제×포맷×채널 성과 집계(밴딧 입력) | (topic_id, format, platform) 유일 |
| `content_item` | 생성 콘텐츠(대본/카피/문안 + 렌더 스펙) | format·status enum |
| `media_asset` | 렌더된 미디어 파일 + checksum | kind enum |
| `publication` | 채널 발행 기록 | status enum |
| `publish_attempt` | 멱등 발행 상태머신 | state enum, 이중발행 차단 |
| `metric_observation` | 지표 관측 시점 | 발행 후 window_index |
| `metric_value` | 지표 값 | **missing XOR value** CHECK |
| `reward` | 사이클/게시물 보상 | reward_value NULL 허용(학습 제외) |
| `playbook` | 학습된 지침 요약 | scope enum |
| `analysis_note` | LLM 분석글(정직 귀인) | insufficient_evidence 플래그 |
| `run_event` | 실행 이벤트 로그(비용 포함) | kind enum, append-only |
| `schema_version` | 마이그레이션 버전 | 전진 순번 |

## 2. 핵심 관계

- `channel 1─* publication` : 한 채널이 여러 게시물을 발행.
- `content_item 1─* publication` : 한 콘텐츠를 여러 채널에 교차 발행 가능(예: 같은 영상 → IG 릴스 + YT 쇼츠). 채널별 발행 행 분리.
- `content_item 1─* media_asset` : 콘텐츠 1개가 이미지/영상/썸네일 등 복수 산출물.
- `cycle 1─* content_item` : 한 사이클이 콘텐츠(들)를 산출.
- `topic 1─* content_item`, `topic 1─* topic_stats` : 주제 풀과 성과 집계.
- `publication 1─1 publish_attempt` : 멱등 발행 상태머신(재시작 복구 지점).
- `publication 1─* metric_observation 1─* metric_value` : 시점별·지표별 정규화 저장.
- `publication 1─0..1 reward` : 관측 창 완료 시 보상 산출(미확정=NULL).

## 3. 결측 처리 (핵심 규칙)

`metric_value`는 **`missing=true`이면 `value IS NULL`, `missing=false`이면 `value IS NOT NULL`**를 CHECK로 강제한다. 지표를 0으로 채우지 않는다 — 플랫폼이 값을 노출하지 않은 것과 실제 0을 구분해야 학습·리포트가 왜곡되지 않는다.

```sql
CONSTRAINT metric_missing_xor
  CHECK ( (missing AND value IS NULL) OR (NOT missing AND value IS NOT NULL) )
```

## 4. 멱등 발행 상태머신 (`publish_attempt`)

```
pending → container_created → published
   │              │
   └──────────────┴──────────→ failed
```

- 발행 진입 전 `publication.status`를 확인(published면 재발행 스킵) → **크래시 재시작 시 이중 발행 0**.
- IG는 2단계(미디어 컨테이너 생성 → 게시)라 중간 상태 `container_created`가 필요.
- 영구 오류(토큰 만료·한도 초과)는 `error_raw`에 원문 보존, 채널 격리(다른 채널 발행 계속).

## 5. 밴딧 학습 입력 (`topic_stats`)

- 행동 공간 = **주제(topic) × 포맷(feed_image/reels/shorts) × 채널(instagram/youtube)**.
- `trials`, `reward_sum`으로 평균 보상 파생(NULL 보상 제외).
- Growth Agent가 이 통계를 읽어 다음 사이클 변형을 선택(선택은 루프 밖 결정론 — 시드 고정).

## 6. LLM 착지점 (제어 채널 분리)

LLM이 기록할 수 있는 테이블은 **열거된 3곳뿐**이다. 그 외 제어 흐름/수치 컬럼은 코드만 기록한다.

1. `content_item.body` — 대본/카피/문안
2. `playbook.guidance` — 성과 요약/지침(서술)
3. `analysis_note.body` — 성과 분석글(서술, 수치 재계산 금지)

지표·reward·상태 전이 등 **수치와 제어는 전부 코드가 계산**한다. LLM은 서술만 한다.

## 7. 마이그레이션 정책

- `schema_version` 전진 순번(정수 또는 타임스탬프 파일명), 트랜잭션 원자성.
- 되돌림은 forward migration(drop/alter)로 처리. 스키마 스냅샷은 CI에서 검증.

## 8. 미결정 (구현 착수 전 확정)

| 항목 | 비고 |
|---|---|
| PK 타입(UUID vs bigint identity) | 초안은 UUID. 성능·조인 고려해 확정 |
| ORM(raw asyncpg vs SQLAlchemy) | ERD와 무관, 착수 시 결정 |
| `goal` 프리셋을 테이블로 물질화할지 | 초안은 `cycle.goal_ref` 문자열. 프리셋 수 늘면 테이블화 |
| 미디어 저장소(로컬 볼륨 vs 오브젝트 스토리지) | `media_asset.storage_url` 추상화로 흡수 |
| 지표 관측 창(window_index) 시점 정의 | goal·플랫폼별 폴링 스케줄과 함께 확정 |
