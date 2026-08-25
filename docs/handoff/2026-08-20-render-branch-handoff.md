# 렌더 정합 브랜치 인수인계 (2026-08-20)

**브랜치**: `fix/render-capacity-and-timing` · **상태**: PR 전 · **커밋** 8개 · **변경** 26파일 +1,304 / −162

**검증**: `ruff` pass · `mypy sns` strict pass(64 files) · `pytest` **276 passed**(Postgres 가동 시) / 251 passed + 25 skipped(미가동 시)

> 이 문서는 브랜치 작업 내역과 **그 과정에서 발견한 결함·미결**을 팀에 공유하기 위한 것이다.
> 결함 항목은 전부 실측 근거를 함께 적었다. 판정이 필요한 항목은 §4에 모았다.

---

## 1. 한 줄 요약

영상·카드 렌더의 **품질 문제 4건을 고치는 과정에서, 잠복해 있던 결함 9건과 문서–코드 불일치 2건을 발견**했다.
그중 **2건은 실험 타당성 자체를 위협**한다(§4 D-1, D-2).

---

## 2. 무엇이 바뀌었나

| 커밋 | 내용 |
|---|---|
| `1ddff8b` | `.env` 규약 정착(`.env.example` 신설), TTS **ADC 인증 경로** 추가, 한 사이클 관통 스크립트 |
| `3c57a7d` | 카드 오버플로를 **파싱 단계에서 차단**, 영상 **컷 = 나레이션 문장** 단위로 분리 |
| `f94f19a` | **테스트 DB 분리** — 개발 DB 파괴 방지 가드 + `_test` DB 자동 생성 |
| `ce6a67b` | 화면 세그먼트 **4초 상한을 렌더러가 강제**(FR-A2) |
| `2044b12` | concat 목록이 세그먼트 수와 어긋나 **영상 뒷부분이 잘리던 버그** |
| `98d77b0` | **균형 줄바꿈**, 액센트 바 필터 이관, **진행바 실제 동작** |
| `ac749e6` | 액센트 바 펄스 제거 — 고정 기준선으로 되돌림 |
| `643452e` | 머지 후속 정합 — `GEMINI_API_KEY` 통일, 카드 폰트 폴백 제거, env 템플릿 보강 |

### 신설 모듈

- `sns/render/text.py` — `display_width` · `split_sentences` · `wrap_balanced` (카드·영상 공용)
- `sns/render/fonts.py` — CJK 폰트 해석 · `FontNotFoundError` (카드·영상 공용)
- `tests/dbguard.py` — 테스트 DB 판별·안전장치

카드와 영상에 복붙돼 있던 `_wrap` 두 벌을 `render/text.py`로 합쳤다
(`video/renderer.py` 주석의 *"공용 유틸로 승격 예정"* 이행).

---

## 3. 개선 실측치

### 영상 화면 전환 (FR-A2: 2~4초)

| | 최초 | 문장 컷 후 | 세그먼트 강제 후 |
|---|---|---|---|
| 화면 전환 단위 | 5 | 16 | **19** |
| 컷당 평균 | 8.7초 | 3.0초 | **2.8초** |
| **4초 초과** | **5/5 (전부)** | 1/16 | **0/19** |

대본 문장은 하나도 버리지 않았다. 컷만 쪼갰다.

### 카드 용량 (이진 탐색 실측)

훅이 지배 변수다 — **훅이 상한을 넘으면 본문 길이와 무관하게 카드가 깨진다.**
상한은 표시 폭 기준(한글 1자 = 2, 라틴 1자 = 1)으로 잡았다. 글자수로 재면 영문 35자와 한글 32자가
같은 취급인데 실제 렌더 폭은 한글이 2배라, 기존 영문 픽스처 8건이 과잉 거부됐다.

### TTS 발화 속도 (Chirp 3 HD 한국어, 12문장)

```
초 ≈ 0.0666 × 표시폭 + 0.70      (고정 오버헤드 0.7초 = 앞뒤 호흡)
폭/초 : 최소 6.1 ~ 최대 15.9      (2.6배 편차)
```

숫자·기호가 특히 느리다. `"실측하면 0.8초가 0.00002초로"` = 폭 40인데 **5.12초**(회귀 예측 3.4초).
소수점을 `"영 점 영영영영이"`로 풀어 써봤으나 **오히려 길어졌다**(5.80 → 6.52초) — TTS가 이미 그렇게 읽고 있다.

**결론: 텍스트로 발화 길이를 예측·통제할 수 없다.** 그래서 상한을 조이는 대신 실측 WAV 길이로 렌더러가 화면을 나눈다.

---

## 4. 발견한 결함 — 판정 필요

### D-1. hybrid 사람 관문이 코드에 없다 · **실험 타당성 위협**

> **[사후 무효]** 이 결함은 승인 관문 구현(C9)으로 한 번 닫혔다가, 이후 발행 모드 3분류(수동·반자동·자동) 자체가 제거되면서 대상이 사라졌다. 아래는 당시 기록이다.

```sql
-- sns/publish/runner.py:_SELECT_PENDING
WHERE p.status = 'pending'
```

발행 러너가 `channel.mode`도 `content_item.status`도 읽지 않는다.
`run_cycle`이 hybrid 채널에 `content_item.status='needs_review'`를 찍지만 **아무도 그 값을 보지 않는다.**

- **FR-Q3 위반** — 수용기준이 *"미승인 자산 발행 안 됨"*인데 뚫려 있다
- **실험 무효화 위험** — auto vs hybrid 비교가 이 프로젝트의 핵심 질문인데, hybrid가 사실상 auto로 동작하면
  3주 뒤 비교 리포트가 "auto vs auto"가 된다

> 출처 대조: `docs/plan/05-콘텐츠생성-품질게이트.md` FR-Q3 · `docs/plan/01-실험설계.md` FR-E1
> vs `sns/publish/runner.py:_SELECT_PENDING`

### D-2. `channel` 테이블이 ERD와 다르다

| 초판 ERD (`bf54046`, gyu, 08-10) | 실제 스키마 (`e734445`, iseul011, 08-12) |
|---|---|
| `status: active｜warming｜blocked｜revoked` | `active｜paused｜revoked` |
| `account_ref` (`ig_user_id｜yt_channel_id`) | **없음** |
| `token_ciphertext` / `key_version` / `created_ts` | `token_encrypted` / `token_key_version` / `created_at` |

**결정 기록이 없다.** T0-2 커밋 메시지는 스키마 결정을 성실히 남겼지만(`PK=UUID, raw SQL+psycopg 확정`)
`channel.status` 값 변경은 언급이 없다.

원인 추정(확증 아님): 커밋 메시지가 *"11-데이터모델 기준"*이라 적혀 있는데,
`11-데이터모델.md:10`은 **"platform·status enum"이라고만 쓰고 값을 나열하지 않는다.**
값이 적힌 곳은 ERD 다이어그램 하나뿐이었다.

**영향 2가지**
- `account_ref` 누락 → 계정 4개(IG×2, YT×2)를 한 DB로 운영할 때 **채널별 외부 계정 식별자가 없다**
- `warming` 소실 → `07-발행.md` §3의 *"계정 개설 후 워밍업(초기 저빈도)"* 정책을 **담을 자리가 없다**

`channel.status`를 읽는 코드는 현재 **0건**이다(컬럼만 있고 죽어 있음).

### D-3. 렌더 결정론이 실제로는 성립하지 않는다 (FR-M1)

같은 문장을 3회 합성한 결과:

```
1회: 4.76초  sha256=57438def…
2회: 4.72초  sha256=f568f093…
3회: 4.72초  sha256=f568f093…
```

**TTS가 비결정론이다.** 따라서 `render_video`의 "같은 spec → 같은 mp4 바이트" 보장은
TTS를 캐시하거나 가짜로 주입할 때만 성립한다. `video/media.py` docstring이 이미 예상해둔 사항이나 **실측으로 확인**됐다.
카드(Pillow)는 여전히 결정론이 성립한다.

---

## 5. 고친 잠복 결함 (판정 불필요, 참고용)

| # | 결함 | 근거 |
|---|---|---|
| F-1 | **영상 뒷부분이 잘렸다** — concat 목록이 `len(spec.slides)`를 써서 렌더된 세그먼트 일부가 버려짐 | 영상 44.20초 vs 오디오 50.76초, 누락 6.56초 = 마지막 두 세그먼트 |
| F-2 | **진행바가 한 번도 동작한 적 없다** — `drawbox` 표현식의 `t`는 타임스탬프가 아니라 **선 두께** | 2·12·24·36·45초 전부 100% 고정. `overlay`로 교체 후 12→50→87% 확인 |
| F-3 | **카드가 CJK 폰트 없으면 두부(□)로 렌더** — C4가 영상만 고치고 카드는 남아 있었음 | `card/renderer.py`의 `load_default` 폴백 |
| F-4 | **`GEMINI_API_KEY` / `GOOGLE_API_KEY` 분열** — 하나만 설정하면 다른 쪽이 조용히 죽음 | C1은 `GEMINI_`, 에이전트는 `GOOGLE_` |
| F-5 | **테스트가 개발 DB를 TRUNCATE** — 작업 중이던 원장이 통째로 소멸 | 실제로 겪음. `tests/dbguard.py`로 차단 |

**F-1·F-2가 테스트를 통과했던 이유**가 중요하다.
기존 단언이 `duration_s == sum(cut_durations_s)`처럼 **둘 다 WAV에서 계산한 값**을 비교하는 자기참조였다.
산출 mp4를 한 번도 보지 않아 영상이 잘려도 통과했다.
→ **ffprobe로 실제 스트림 길이를 재고, 프레임을 뽑아 픽셀을 세는 테스트**로 교체했다.

---

## 6. 팀원 액션 아이템

### 필수

1. **`.env`의 `GOOGLE_API_KEY` → `GEMINI_API_KEY`로 이름 변경**
   값은 그대로. 안 바꾸면 에이전트가 기동하지 않는다.
2. **`.env.example` 재확인** — C1의 트렌드 소스 키 3종(`NAVER_CLIENT_ID/SECRET`, `YOUTUBE_API_KEY`)이 추가됐다.
   없으면 해당 소스가 **조용히 비활성화**된다(미등록 → `ok=False` 격리라 에러도 안 난다).
3. **테스트는 이제 `sns_test` DB를 쓴다** — 자동 생성되므로 별도 설정 불필요.
   개발 DB(`sns`)는 더 이상 `pytest`에 파괴되지 않는다.

### 알아둘 것

- **Gemini 무료 티어 일일 20건** (`gemini-3.5-flash`). 사이클당 2~3콜이라 하루 6~10 사이클이 상한.
  계정 4개 × 일 1건 운영에는 빠듯하다 → FR-P6 비용 상한 계산에 반영 필요.
- **이미지 생성은 유료 전용** — Gemini 이미지 모델 6종 전부 무료 티어 한도 **0**. 과금 계정 없이는 호출 불가.
- **Chirp 3 HD TTS는 무료 티어가 없다**(월 100만 자 무료는 표준·WaveNet 음성 기준).
- **영상 렌더에 ffmpeg/ffprobe가 PATH에 필요**하다. 없으면 해당 테스트 5건이 skip된다.

---

## 7. 코드 리뷰 시 봐주실 것

- `FontNotFoundError`의 상위 클래스를 `VideoRenderError` → `RuntimeError`로 바꿨다(카드도 같은 예외를 던져야 해서).
  **C4 담당자 영역**이라 확인 필요. 잡는 곳이 없어 동작 영향은 없다.
- `VideoRender.slide_durations_s` → `cut_durations_s` **이름 변경**(의미가 컷 단위로 바뀜). 외부 사용처는 테스트뿐이었다.
- 카드 용량 상한 5종은 **실측값**이다. 상한을 꽉 채운 스펙이 넘치지 않음을
  `test_capacity_limits_prevent_render_overflow`가 앵커로 고정한다 — 값을 바꾸려면 이 테스트를 함께 봐야 한다.

---

## 8. 남은 작업

### 진행 중 (미완)

**무인 발행 스케줄러** — 기획 단계. `superpowers`/planning-harness로 요구사항 분해까지 진행했고
**미결 11건**이 남아 게이트 2 미통과 상태다. 확정된 것은 실행 인프라를 **A(상주 러너) → 이후 B(GHA cron)/C(VM)**로
간다는 것뿐이다(사용자 확인 2026-08-20).

현재 유튜브 자동 업로드는 **부품은 있으나 배선이 없다**:

| 단계 | 상태 |
|---|---|
| 영상 생성 · 품질 게이트 · DB 원장 | ✅ |
| YouTube 업로드 **어댑터** | ✅ (`videos().insert()` 리줌 업로드, 실 업로드 성공 이력 있음) |
| 플랫폼 → 어댑터 **디스패처** | ❌ 없음 |
| **자동 트리거**(스케줄) | ❌ 없음 |

### 미착수

- **인스타그램 트랙 전체** — `sns/adapters/instagram/`이 **0줄**. IG-1(Meta 앱 권한, 최장 리드타임)부터 미확인
- **C7 학습 루프** — `learning/`에 `validator.py`만. RewardFn·`topic_stats` 갱신·playbook 영속화 없음
- **C9/C10 웹** — `sns/web/` 빈 껍데기
- **`read_stats` 운영 구현** — `FakeReadStats`만 존재. 실 LLM을 붙여도 "과거 데이터 기반 학습"은 아직 반쪽

### 품질 관련 미해결

- **FR-Q5/Q6 정성 품질 스파이크 미실행** — `docs/spikes/`에 렌더러 선택·deepagents 스파이크뿐.
  포맷별 5~8건을 리뷰어 2인이 독립 판정하는 절차가 아직 없다
- **템플릿이 1종뿐** — FR-E2가 "템플릿 **풀**"을 통제변수로 규정하는데 실제로는 1개.
  변형 선택 로직이 코드 어디에도 없어, 100건을 만들면 글자만 다른 같은 영상 100개가 나온다
  (카드뉴스형 5종 시안은 `scripts/out/spike-templates/`에 있음 — 전부 스파이크용 버릴 코드)

---

## 9. 재현 방법

```bash
uv sync
cp .env.example .env          # GEMINI_API_KEY 채우기
docker compose up -d postgres
uv run pytest                 # 276 passed (ffmpeg PATH 필요)

# 한 사이클 실물 관통 — 실 트렌드 → 실 LLM → 실 렌더 → 실 원장
uv run python scripts/e2e_cycle.py
```

TTS는 API 키 대신 **ADC**로도 동작한다(조직 정책이 API 키 발급을 막는 계정용):

```bash
gcloud auth application-default login
gcloud services enable texttospeech.googleapis.com --project=<PROJECT>
```
