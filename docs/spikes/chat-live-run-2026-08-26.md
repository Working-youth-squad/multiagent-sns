# 키워드 챗봇 실 LLM 관통 기록 (2026-08-26)

FR-W6 구현을 **실제 LLM**으로 돌린 기록. 오프라인 테스트(`ScriptedChatModel`)는 배선만
검증한다 — 표시 규율을 지키는 주체가 LLM인 부분은 실 모델로만 확인된다.

| 항목 | 값 |
|---|---|
| 프로바이더 | `SNS_MODEL_PROVIDER=openai` |
| 모델 | **`gpt-5-nano`** — 계정에서 조회 가능한 최저가 계층 |
| 경로 | `sns/web/chat/app.py` 전체 관통(폼 POST → DB → 렌더), 저장소만 InMemory |
| 키워드 조회 | 실제 3소스(자동완성, 무인증) |

**최저가 계층을 고른 것은 의도적이다.** 규율이 프롬프트가 아니라 **구조**로 지켜지는지
보려면 가장 약한 모델이 가장 좋은 시험대다.

## 1. 멀티턴 — 맥락이 DB를 왕복해도 이어지는가

| 턴 | 입력 | 관측 |
|---|---|---|
| 1 | "개발자 취업" | `search_keywords("개발자 취업")` · `filter_mode=active` · 후보 10 · 미판정 14 |
| 2 | "그 중에 포트폴리오 쪽이 궁금해" | **`search_keywords("개발자 포트폴리오")`** — 앞 턴 맥락을 이어받아 질의어를 좁혔다 |
| 3 | "좋아, 그걸로 콘텐츠 만들어줘" | 후보 3개 중 어느 것인지 되물음 — `confirm_topic` 미호출 |
| 4 | "개발자 포트폴리오 작성법으로 확정" | `confirm_topic` → `seed_topic(source="chat_seed")` |

2번이 이 설계의 핵심 증거다. 대화 이력은 hidden input이 아니라 DB에서 복원되는데
(`to_langchain_messages`), 그 복원본만으로 모델이 "포트폴리오"를 앞 대화에 붙여 읽었다.

3번도 의도한 대로다 — "그걸로"가 셋 중 모호할 때 동의 없이 확정하지 않았다(프롬프트 규칙 5).

## 2. 규율 2 — `passthrough`를 "걸렀다"로 뭉개는가

가장 위험한 경우를 고정 입력으로 만들어 물었다: 소스 1곳만 성공, 밴드 미개방, 전량 미판정.

입력 사실: `filter_mode=passthrough` · 미판정 3건 · 실패 소스 `google_suggest`,`youtube_suggest`

`gpt-5-nano` 답변(발췌):

> - 필터 열리지 않음: 데이터가 적어 필터가 열리지 않았고, 전체 데이터를 사용했습니다.
> - 실패 소스: google_suggest, youtube_suggest
> - 성공 소스: 네이버 자동완성(naver_autocomplete)

**정확하다.** 규율 2(필터 상태)와 규율 4(소스 실패)를 최저가 모델이 그대로 지켰다.

규율 1(수치)은 모델이 어길 수단 자체가 없다 — `summarize_for_model`이 `rank_std`·
`observed_mean`을 애초에 넘기지 않는다. 이것이 프롬프트로 부탁하지 않고 구조로 막은 몫이다.

## 3. 고친 것 — 확정 후 챗봇이 초안을 직접 썼다

첫 관통에서 `confirm_topic` 직후 모델이 제목·개요·주요 구성까지 **콘텐츠 초안을 대화에
써버렸다**(답변 길이 1,000자+). 초안은 Content Agent 몫이고, 승인 화면에 올라갈 글과 다른
글이 대화에 남으면 사용자는 어느 쪽이 발행되는지 알 수 없다.

프롬프트 규칙 6 신설 + `confirm_topic` 툴 반환문에 금지를 명시해 고쳤다.
재실행 결과 답변 길이 **155자**, 확정 사실과 확인 위치만 안내한다.

## 4. 대화 층에서 관찰된 것

- 모델이 `category`를 자유 문자열로 짓는다(관측: `"portfolio"`). `TopicResult.category`가
  `str`인 것은 온보딩 채널을 위한 의도된 폭이고, `save_topic`은 category를 저장하지 않아
  원장 오염은 없다. 다만 Content Agent 프롬프트에는 그대로 들어간다.
- 후보가 0건일 때 모델이 "데이터 부족"이라는 표현을 쓴다. 툴이 준 문장("후보 없음")의
  드리프트지만 사실을 뒤집지는 않는다.

## 5. 콘텐츠 사이클 실제 완주 (Postgres + 카드 렌더)

`docker compose up -d postgres` + 마이그 004 적용 후 시드 주제로 `run_cycle`을 돌렸다.

| 항목 | 값 |
|---|---|
| 소요 | **337초** (gpt-5-nano, 콘텐츠 생성 + 카드 렌더 + 게이트) |
| 결과 | `status=completed` · `prepared=1` |
| 원장 | `agent="seed"` — Topic Agent를 부르지 않았음이 기록됨 |
| 착지 | `content_item.status=needs_review` · `media.quality_status=passed` · `publication=pending` |
| 렌더 | 1080×1350 PNG 47,491바이트, 한글 정상(malgun.ttf) |
| 승인 화면 | :8001에 "승인 대기 (1건)"으로 노출, 딥링크 200 |

### 여기서 잡은 표시 함정

`media.quality_status=passed`인데 `content_item.status=needs_review`다. 초안 카드가 미디어
품질만 뱃지로 보여주면 **"품질 통과"**로 읽혀 발행된 줄 안다 — 실제로 발행을 막고 있는 것은
승인 상태다. 주 뱃지를 `content_status`로 바꾸고("승인 대기"), 품질은 문제가 있을 때만
덧붙이게 고쳤다.

썸네일도 `object-fit:cover`라 4:5 카드의 훅 문구가 잘렸다 — 발행될 것의 미리보기이므로
`contain`으로 바꿔 전체가 보이게 했다.

## 6. 아직 검증되지 않은 것

- **미검증**: 실제 발행(`run_pending_publications`). 승인 대기까지가 이 기록의 범위다.
  사람이 승인 화면에서 승인해야 그다음이 돈다 — 설계상 의도된 관문이다.

## 재현

```bash
# .env: OPENAI_API_KEY=...
docker compose up -d postgres
DATABASE_URL=postgresql://sns:sns@localhost:5432/sns uv run python -m sns.db.migrate
DATABASE_URL=postgresql://sns:sns@localhost:5432/sns SNS_MODEL_PROVIDER=openai \n  uv run python scripts/run_chat_web.py     # :8003
DATABASE_URL=postgresql://sns:sns@localhost:5432/sns \n  uv run python scripts/run_approve_web.py  # :8001 — 초안 카드의 링크 대상
```
