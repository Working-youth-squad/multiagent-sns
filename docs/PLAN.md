# multiagent-sns — 기획서

> 멀티에이전트(deepagents) 기반 자율 SNS 성장 엔진 · 작성 2026-08-10 · 상태: 기획(제안)
>
> 이 문서는 **팀원이 task를 나누어 개발에 착수할 수 있도록** 작성한 기획서다.
> 세부 요구사항은 [`SPEC.md`](SPEC.md), 데이터 모델은 [`ERD.md`](ERD.md), 다이어그램은 [`diagrams/`](diagrams/).
>
> ⚠️ **그린필드**: 과거 프로젝트의 코드·스펙 자산을 일체 재사용하지 않고, 이 레포에서 처음부터 신규 구현한다.

---

## 1. 한 줄 정의

**개발자 주제 콘텐츠**를 **IG 피드 게시물 · IG 릴스 · YouTube 쇼츠**에 **딥에이전트가 자동으로 기획·제작·발행**하고, **반응(지표)을 학습**해 **채널을 키우는** 멀티에이전트 자율 성장 엔진.

## 2. 배경 · 목표

- 개발자 브랜딩/채널 성장을 사람이 매번 기획·제작·발행·분석하는 것은 반복 비용이 크다.
- 이를 **자율 에이전트 루프**로 돌려: 주제 발굴 → 콘텐츠 제작 → 멀티채널 발행 → 반응 수집 → 학습 → 다음 콘텐츠 개선을 자동화한다.
- **핵심 성과 지표**: 자기 채널의 시간에 따른 성장(조회수·팔로워·저장·시청유지 등 goal 기준 **기울기**). 타 계정과의 절대 비교는 하지 않는다(과장 방지).

## 3. 범위

**포함(이번 라운드)**
- 채널: IG 피드(카드뉴스 이미지) · IG 릴스(영상) · YouTube 쇼츠(영상) — **전부 공식 API**.
- 영상: **템플릿 코드 합성**(자막+슬라이드+TTS, ffmpeg/moviepy). 생성형 비디오 모델 미사용.
- 코어: **deepagents 프레임워크 그대로 도입**(Orchestrator + 서브에이전트).
- 학습: 반응 지표 기반 밴딧(주제×포맷×채널 선택) + 플레이북 갱신 + LLM 분석글(정직 귀인).
- 운영: 상주 러너(스케줄러) + PostgreSQL + Docker Compose + GitHub Actions CI. 웹 대시보드는 선택.
- 대상: **단일 운영자/팀의 자기 채널**.

**미포함(후속)**
- 다중 사용자 SaaS(다중테넌트)·타인 계정 대행 → Meta/Google App Review 게이트 발생, 이번 범위 밖.
- TikTok 등 추가 채널, 생성형 비디오, 실시간 라이브.

## 4. 시스템 아키텍처

전체 그림: [`diagrams/architecture.mermaid`](diagrams/architecture.mermaid)

```
슬롯 스케줄러(러너)
      │ 사이클 트리거
      ▼
Orchestrator (deepagents main)
  ├─ Topic Agent     주제 발굴(트렌드·플레이북 참조)
  ├─ Content Agent   대본/카피/카드 문안 (LLM)
  ├─ Media Agent     이미지 카드 / 영상 템플릿 합성 (결정론 렌더)
  ├─ Publisher Agent IG Graph API · YouTube Data API (멱등 발행)
  ├─ Analyst Agent   지표 폴링 → reward → 분석글(정직 귀인)
  └─ Growth Agent    밴딧: 다음 사이클 변형(주제×포맷×채널×시각) 선택
      │
      ▼
PostgreSQL (단일 DB) ── 웹 대시보드(선택)
```

**제어 채널 분리**: 통제=코드(상태·수치·스케줄) · 판단=LLM(주제/글/분석 서술) · 경계=툴 타입 계약 + DB 착지점(3곳). LLM은 열거된 곳에만 기록하고 수치는 코드가 계산한다.

## 5. 멀티에이전트 구성 (deepagents)

| 에이전트 | 책임 | 입력 | 산출(착지점) |
|---|---|---|---|
| **Orchestrator** | 사이클 계획·서브에이전트 위임·이벤트 기록 | 스케줄 트리거 | `cycle`, `run_event` |
| **Topic** | 개발자 주제 발굴 | `topic_stats`, `playbook` | `topic`, `content_item.topic_id` |
| **Content** | 포맷별 대본/카피/문안 작성 | 주제, 플레이북 | `content_item.body`, `media_spec` |
| **Media** | 이미지 카드 / 영상 합성(결정론) | `media_spec` | `media_asset`(checksum) |
| **Publisher** | 채널별 발행(멱등) | `content_item`, `channel` | `publication`, `publish_attempt` |
| **Analyst** | 지표 폴링·reward·분석글 | 발행 이력 | `metric_*`, `reward`, `analysis_note` |
| **Growth** | 밴딧으로 다음 변형 선택(결정론) | `topic_stats` | `cycle.decision` |

발행 사이클: [`diagrams/publish-cycle-sequence.mermaid`](diagrams/publish-cycle-sequence.mermaid)
학습 루프: [`diagrams/learning-loop-sequence.mermaid`](diagrams/learning-loop-sequence.mermaid)
운영자 플로우: [`diagrams/user-flow.mermaid`](diagrams/user-flow.mermaid)

## 6. 기술 스택 (제안)

| 층 | 선택 | 비고 |
|---|---|---|
| 언어 | Python 3.12 | |
| 에이전트 | **deepagents** (LangGraph 기반) | 코어 그대로 도입 |
| LLM | **Claude** (`claude-sonnet-5`) | 주입식(테스트 시 가짜 모델) |
| DB | **PostgreSQL** | 단일 인스턴스, 정규화+CHECK |
| 이미지 | Pillow | 결정론 카드 렌더 |
| 영상 | ffmpeg / moviepy + TTS | 템플릿 합성 |
| 발행 | Instagram Graph API · YouTube Data API v3 | 공식 API만 |
| 스케줄 | 상주 러너(APScheduler/컨테이너 cron) | 슬롯·폴링 |
| 인프라 | **Docker Compose** | k8s 미사용 |
| CI/CD | **GitHub Actions** | pytest+lint+타입 |
| 웹(선택) | FastAPI + 경량 프론트 | 대시보드 |

> 스택 세부(TTS 엔진, ORM, 웹 프레임워크)는 [`SPEC.md` §6 미결정](SPEC.md) 참조.

## 7. 개발 원칙 (팀 공통)

1. **공식 API만** — 비공식 스크래핑/자동화 금지(계정 정지 리스크).
2. **결정론 재현** — 모델·시계·transport 주입, 시드 고정. 가짜 모델로 사이클 재현 테스트.
3. **멱등 발행** — 이중 발행 0(상태머신 + 재시작 복구).
4. **missing=NULL** — 지표 결측을 0으로 채우지 않음(DB CHECK 강제).
5. **정직 귀인** — LLM 분석글은 근거 있을 때만 인과 주장, 없으면 "근거 부족". 수치는 코드만.
6. **자기 베이스라인 성장만** — 타 계정 절대 비교 금지, 한계 정직 표기.
7. **시크릿 암호화** — 토큰 평문 저장 금지.
8. **thin spec** — 경계·데이터·측정만 고정, 에이전트 내부 행동은 프롬프트/플레이북에.

## 8. 모듈 · Task 분할 (팀 협업 단위)

각 모듈은 독립 개발 가능하도록 **툴 계약/DB 스키마를 seam**으로 분리했다. 담당자는 협의로 배정.

| # | 모듈 | 산출물 | 의존 | 예상 난이도 |
|---|---|---|---|---|
| **M1** | 인프라·DB | 스키마·마이그레이션, Docker Compose, config, 시크릿 암호화 | — | 중 |
| **M2** | 에이전트 코어 | deepagents Orchestrator+서브에이전트 골격, 툴 계약, LLM 주입, 결정론 하네스 | M1 | 상 |
| **M3** | 콘텐츠 생성 | Topic/Content 에이전트 프롬프트, `media_spec` 스키마 | M2 | 중 |
| **M4** | 미디어 렌더 | Pillow 카드 렌더 + ffmpeg/TTS 영상 합성, 규격 검증 | M1 | 상 |
| **M5** | 발행 어댑터 | IG Graph API + YouTube Data API 클라이언트, 멱등 원장, 오류 분류 | M1 | 상 |
| **M6** | 지표·학습 | 폴러, RewardFn, 밴딧, topic_stats/playbook, 분석글+검증기 | M1, M5 | 상 |
| **M7** | 웹 대시보드(선택) | 온보딩·설정·리포트·알림 | M1, M6 | 중 |
| **M8** | CI/CD·관측 | GitHub Actions, 로깅, run_event 대시 | M1 | 하 |

**병렬화 전략(seam-first)**: 먼저 M1(DB 스키마 + 툴 계약 타입)을 확정해 seam을 고정하면, M3~M6을 가짜(fake) 어댑터/모델로 병렬 개발할 수 있다. 실 API 배선은 각 모듈이 seam 뒤에서 독립적으로.

## 9. 로드맵 (제안)

- **Phase 0 — seam 확정**: M1 스키마 + 툴 타입 계약 + 결정론 하네스. (팀 킥오프)
- **Phase 1 — M1(실환경 관통)**: IG 피드/릴스 + YT 쇼츠 각 1건 자동 발행 → 지표 → reward → 리포트까지 한 번 관통(수동 트리거 허용).
- **Phase 2 — 자율 루프**: 스케줄러 상주 + 밴딧 학습 + 플레이북 반영 + 분석글.
- **Phase 3 — 운영 성숙**: 대시보드, 알림, 비용/한도 관리, 계정 안전 운영.
- **Phase 4(후속)**: 채널 확장·다중 계정 등 범위 확대 검토.

## 10. 리스크

| 리스크 | 대응 |
|---|---|
| 플랫폼 API 한도/정책 변동(특히 YouTube 업로드 쿼터) | 발행 한도 존중·스케줄 분산, 구현 전 최신 한도 재확인([`SPEC.md` §7](SPEC.md)) |
| deepagents 코어의 비결정성(LLM 루프) | 모델/시계 주입 + 착지점 고정 + 결정론 재현 테스트로 감쌈 |
| 계정 안전(자동 발행 → 정지 위험) | 공식 API·워밍업·보수적 빈도, 비공식 자동화 금지 |
| 영상 품질(템플릿 합성의 한계) | 디자인 시스템·템플릿 다양화, 반응 학습으로 개선 |
| 콜드 스타트(초기 학습 데이터 없음) | 균등 탐색(sweep-first)으로 시작, 베이스라인 국면 |

## 11. 산출물 지도

- [`PLAN.md`](PLAN.md) — 본 기획서(개요·아키텍처·task 분할·로드맵)
- [`SPEC.md`](SPEC.md) — FR/NFR·예외/테스트 케이스·DoD·미결정·외부 제약
- [`ERD.md`](ERD.md) — 데이터 모델 상세
- [`diagrams/`](diagrams/) — architecture · publish-cycle · learning-loop · user-flow · erd (Mermaid)
