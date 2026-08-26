# 영상 트랙 → 웹페이지 — 진행 기록 (2026-08-26)

**목표**: 팀 4방향 영상 기능을 웹페이지에서 쓸 수 있게 하고, **수동 영상 다운로드**와
**유튜브 e2e**까지 시연 영상으로 찍는다.

**상태**: 코드 완료. 챗봇(:8003)에서 대화로 포맷·제작 방식을 고르고, 영상이 초안 카드에
재생기로 뜨고, mp4로 내려받히고, 유튜브 실 업로드 배선이 붙어 있다.

**이 개발 환경에서는 영상이 안 켜진다** — TTS 권한이 없다(§5). 코드 문제가 아니다.

---

## 0. ⚠️ 시연 전에 반드시 볼 것

### 유튜브는 API 키로 업로드할 수 없다

`sns/adapters/youtube/auth.py`가 요구하는 것은:

1. `.secrets/client_secret.json` — **OAuth 데스크톱 앱 클라이언트**(GCP 콘솔에서 수동 발급)
2. 첫 실행 시 **브라우저 동의**(`flow.run_local_server`) → `token.json` 저장

구글 제약이지 이 레포 선택이 아니다. 첫 실전은 `privacy_status="private"` 고정이므로
(`youtube/publisher.py`) 시연에서 "공개 게시물"은 나오지 않는다.

**배선은 끝나 있다.** 팀원이 `token.json`을 만들어 두고 `CHAT_PUBLISH=youtube`로 띄우면
승인된 건이 실제로 올라간다. 서버 **기동 시점**에 자격을 확보하므로(동의 창이 백그라운드
스레드에서 뜨면 사용자는 대화만 보고 있어 영영 모른다), 촬영 전에 한 번 띄워 두면 된다.

### 나레이션(TTS) 자격증명이 있어야 영상이 대화에 뜬다

`GOOGLE_TTS_API_KEY` 또는 ADC. **없으면 대화 목록에서 영상이 아예 빠진다**(§2 Capability
Gate). 이건 기능이다 — 없는 것을 띄우면 사용자가 고른 뒤 분 단위를 기다린 끝에 실패를
만난다.

---

## 1. 4기능이 지금 어디 있나

| # | 기능 | 위치 |
|---|---|---|
| ① | 영상 클립 자체 생성 (`generated_clip`) | **main에 머지됨** (PR #43, `9167f84`). `style="clip"` + `clip_ref` |
| ② | 장면 이미지 + ffmpeg (`generated_scene`) | main |
| ③ | 영상+나레이션 omni 출력 | **없음** — 팀원이 나중에 올린다 |
| ④ | Gemini가 spec·장면 대본 → 영상 | main (`sns/agents/content.py` `scene_prompt`) |

**축이 둘이라는 것을 헷갈리지 말 것**:
- `method` = 어느 트랙인가(재료 출처). `template` / `generated_scene` / `generated_clip`
- `style` = 그 트랙 안의 화면 문법. `3col` / `motion` / `clip`

PR #43이 넣은 것은 **style `clip`**이다(배경이 영상). `method` `generated_clip`은
`tools/contracts.py`에 이름만 있고 렌더러가 없어 라우터가 거부한다 — 정상이다.
렌더러가 오면 `sns/runner/wiring.py`의 `build_render_wiring` dict에 한 줄 추가하면 켜진다.

---

## 2. 무엇이 만들어졌나

### 대화에서 고른다 (`sns/chat/agent.py`)

`confirm_topic(title, summary, category, content_format, method)`.
`run_chat_turn(..., formats=..., methods=...)`로 **배선된 것만** 넘긴다 — 시스템 프롬프트의
선택지 목록이 그 값에서 생성되고(`_capabilities_block`), 목록 밖의 값은 툴이 오류 문자열로
되돌려 보낸다(LLM이 재시도). **카드로 조용히 떨구지 않는다** — 그러면 사용자가 영상을
골랐는데 이미지가 나오고 왜인지는 아무 데도 안 남는다.

산출은 `SeedRequest(topic, content_format, method)`. `StartCycleFn`이 이걸 받는다
(예전엔 `TopicResult`만 받아서 배선이 포맷을 알 길이 없었고, 그래서 하드코딩됐다).

### 배선 정본 (`sns/runner/wiring.py` — 신설)

`build_render_wiring(kind="card"|"video", ...)` → `(render_media, assess_quality,
resolve_media_spec, supported_methods)`. **프로필 CLI와 챗봇 웹이 같은 함수를 부른다.**

`ContentFormat`이 아니라 `FormatChoice`를 받는다 — 릴스와 쇼츠는 배선이 같고, 다른 것은
규격뿐이라 spec이 정한다. `ContentFormat`을 받으면 인스타 릴스 + 유튜브 쇼츠를 한 사이클에
태울 때 "어느 대상의 포맷을 넘길 것인가"라는 답 없는 질문이 생긴다.

### 화면 (`sns/web/chat/render.py`)

`_media_tag(asset_id, media_kind)`가 `<img>` ↔ `<video controls>`를 가른다. 종류는
**원장이 준다**(`DraftItem.media_kind` · `ExportItem.media_kind`) — 화면에는 저장소 URL이
오지 않아(`/media/{id}` 중계) 확장자로 추측할 수 없다. `ExportItem.media_ext`는
`media_kind`에서 파생하는 프로퍼티다(둘을 따로 나르면 mp4를 .png로 내려주는 날이 온다).

### 발행 (`sns/publish/router.py` — 신설)

`PlatformPublishRouter`. 유튜브 어댑터를 그대로 러너에 물리면 대기 큐의 인스타 건에서
`ValueError`가 나 **루프 전체가 끊긴다**(채널 격리 FR-P4가 반대로 깨진다). 라우터는
던지지 않고 재시도 가능(transient) 오류를 돌려주므로 그 건은 `pending`으로 남는다 —
`failed`/`skipped`로 끝나면 어댑터를 붙인 뒤에도 영영 다시 선택되지 않는다.

### 운영 스위치 (`scripts/run_chat_web.py`)

| env | 기본 | 뜻 |
|---|---|---|
| `CHAT_FFMPEG` | `ffmpeg` | ffmpeg 경로 |
| `CHAT_VIDEO_STYLE` | `motion` | `3col`/`motion`/`clip` |
| `CHAT_VIDEO_METHODS` | `template` | 대화에 띄울 제작 방식. 유료는 **명시해야** 켜진다 |
| `CHAT_PUBLISH` | (없음) | `youtube`면 승인된 건을 실제로 올린다 |

---

## 3. 되돌아온 사고를 하나 잡았다

`scripts/run_profile_cycle.py`에 **배선 블록이 두 벌** 있었다(origin/main에도). 새 블록이
포맷별로 갈라 놓은 값을 옛 블록이 덮어썼다:

- `--format video --style 3col` → 옛 블록의 `else:`가 걸려 **영상 라우터가 카드
  렌더러로 교체**됐다. 영상을 요청했는데 이미지가 나갔다.
- `--style motion` → `resolve`가 `_extras()`(style 못박기)와 `generate=`(캐릭터 장면
  생성)를 잃은 옛 판으로 덮어써졌다.

배선을 `sns/runner/wiring.py` 한 곳으로 옮기면서 옛 블록을 지웠다.
`tests/test_render_wiring.py`가 그 모양을 정확히 겨눈다.

**챗봇 시드도 깨져 있었다** — `topic_major`가 필수가 됐는데(#34 머지)
`run_chat_web.py`가 안 넘겨 `TypeError`였다. 이제 채널 프로필에서 읽는다.

---

## 4. topic_major는 프로필에서 온다 — 없으면 만들지 않는다

`plan_seed`(= `run_chat_web.py`)가 hybrid 채널을 모으면서 온보딩 프로필을 읽는다.

- 프로필 없는 채널은 **빼고, 뺐다는 사실을 대화에 남긴다**
- 전부 없으면 만들지 않고 "온보딩 :8002에서 인터뷰를 완료하세요"로 안내한다
- `topic_major`가 갈리는 채널도 뺀다 — 사이클 하나에 `topic_major` 하나이고
  (`run_cycle`이 대상별이 아니라 사이클별로 받는다), 남의 주제 정책으로 렌더하는 것보다
  안 만드는 편이 낫다

조용한 개발 기본값으로 떨어지면 요리 채널에 코드 컷이 들어간다 — 그래서 그 기본값을
없앤 것이다(`sns/topic_policy.py`).

> 데모 채널 `instagram @chat-seed-demo`에는 프로필(개발/파이썬·커리어)을 넣어 뒀다.

---

## 5. 이 개발 환경의 상태 (2026-08-26 기준)

| 항목 | 상태 |
|---|---|
| ffmpeg / ffprobe | **있음** (PATH) |
| `GEMINI_API_KEY` | **비어 있음** → `SNS_MODEL_PROVIDER=openai` 필요 |
| `OPENAI_API_KEY` | 있음 (`gpt-5-nano`로 관통 확인) |
| `GOOGLE_TTS_API_KEY` | **비어 있음** |
| ADC | 잡히지만 **TTS 권한 없음** — 프로젝트 `signal-alpha-demo`에 403 |
| `PEXELS_API_KEY` | **비어 있음** → 스톡 사진 없이 그라데이션 폴백 |

→ **이 환경에서 영상은 안 켜진다.** 대화에 영상이 안 뜨고, 기동 콘솔이 사유를 찍는다:

```
포맷 : card  (영상 불가 — 나레이션(TTS) 사용 불가 — 403 Caller does not have ...)
```

시연하려면 둘 중 하나:
- `GOOGLE_TTS_API_KEY`를 발급해 `.env`에 넣는다 (가장 빠름)
- ADC 프로젝트에 `roles/serviceusage.serviceUsageConsumer` + TTS API 사용 설정

`PEXELS_API_KEY`도 넣으면 컷 배경에 실사 사진이 붙는다(없어도 영상은 나온다).

---

## 6. 검증한 것

- `pytest` 전량 GREEN · `ruff check` · `ruff format --check` · `mypy sns` 모두 통과
- 신규 테스트: `test_render_wiring.py`(배선 정본) · `test_chat_web_wiring.py`(시드 계획·
  Capability 탐지) · `test_publish_router.py`(플랫폼 라우팅) + 챗봇 에이전트/화면 테스트 확장
- **브라우저 실물 확인**: 초안 카드에 `<video>` 재생기, 내보내기 화면에 재생기 +
  "영상 내려받기", `/media/{id}?download=1` → `content-type: video/mp4` ·
  `filename="verify.mp4"` (검증용 레코드는 확인 후 원장에서 지웠다)
- 실 LLM 사이클: 콘텐츠 에이전트까지 통과하고 **렌더의 TTS 호출에서 403**으로 멈춤 —
  실패가 대화에 사람 문장으로 남는 것까지 확인

**아직 못 본 것**: 진짜 나레이션이 들어간 mp4 한 편. TTS 권한이 풀리면 그때 확인.

---

## 7. 다음 할 일

1. **TTS 자격증명 확보**(§5) → 영상 한 편 실제 렌더 → 승인 화면에서 승인
2. **유튜브 토큰**: `.secrets/client_secret.json` 놓고 한 번 띄워 동의 →
   `CHAT_PUBLISH=youtube`로 재기동 → 승인된 건이 private으로 올라가는 것 확인
3. 시연 촬영. 유튜브가 private 고정이라는 것을 미리 감안할 것
4. (팀원) omni 트랙이 오면 `build_render_wiring`의 dict에 한 줄

---

## 8. 함정

- **CI가 `ruff format --check`를 레포 전체에 돌린다** — 마크다운 안 파이썬 코드블록까지
  포맷 대상이다. 문서만 고쳐도 `uv run ruff format .`.
- **mypy는 `uv run mypy`가 AppLocker에 막힌다** → `uv run python -m mypy sns`.
  CI는 `sns`만 본다 — `scripts/`에는 기존 오류가 몇 개 남아 있다.
- **`uv run pytest -q`가 Postgres가 떠 있으면 통합 테스트를 더 돈다**. 초록이어도
  커버리지가 다르다.
- **한글 파일명·콘솔**: Windows 콘솔이 cp949라 print가 깨진다.
  `sys.stdout.reconfigure(encoding="utf-8")` 관례를 따를 것.
- **워크트리가 10개다** (`git worktree list`) — `cd`만 바꾸면 도구가 다른 워크트리를 본다.
- **비용**: `generated_scene`은 컷마다 유료 이미지 생성(사이클당 12장 상한).
  `CHAT_VIDEO_METHODS`로 명시해야 켜지는 것이 안전장치다 — 기본값으로 두지 말 것.

---

## 9. 미해결 (팀 결정)

hybrid 원고를 손으로 올리면 원장 등록 경로가 없다 — `publication`이 `pending`으로 남는다.
발행 수단(API/손)과 저작 모드(auto/hybrid/manual)가 한 컬럼에 묶인 탓.
후보 3개는 `docs/plan/10-웹-알림.md` §4.4.
