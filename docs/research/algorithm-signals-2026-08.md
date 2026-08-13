# 알고리즘 신호 조사 브리핑 (2026-08) — C8 구현 근거

> 상위: [09-알고리즘-신호-최적화](../plan/09-알고리즘-신호-최적화.md) (FR-A) · 구현: `sns/signals/scoreboard.py`
> 신뢰도 표기: **[공식]** = 플랫폼 1차 출처 / **[보도]** = 공식 발언의 2차 보도 / **[통설]** = 근거 없음 — 서비스 문구에 "공식" 표기 금지

## 1. 결론 — 서비스 기능화 가능

두 플랫폼 모두 **공식 발표된 랭킹 신호가 전부 API로 측정 가능**하다. 신호 모델은 플랫폼별로 다르며(§2·§3), `SIGNAL_DEFS[platform]` 데이터로 구현했다. 단 (a) IG API 메트릭 개편이 잦고 (b) 자동 생성 콘텐츠 정책이 양쪽 모두 강화 중(§5) — 이 둘이 지배적 리스크다.

## 2. 유튜브 쇼츠 신호

**[공식]** (support.google.com/youtube/answer/11914225): 랭킹 신호 = "**노출 시 시청을 선택한 비율**(viewed vs swiped away), **평균 시청 시간**, **평균 시청 비율**" + 좋아요·시청 후 설문(만족도). 쇼츠와 롱폼은 별도 랭킹. **발행 빈도·시간은 랭킹 무관(공식 부인)**. 추천은 영상당 점수가 아니라 시청자별 예측("pull") — 전역 "알고리즘 점수"는 존재하지 않음 [보도: Todd Beaupré, 2025-01].

**API 매핑** (Analytics API v2, `filters=video==ID`):

| 공식 신호 | API 메트릭 | 비고 |
|---|---|---|
| 시청 선택률 | `engagedViews / views` 근사 | "viewed vs swiped"는 스튜디오 전용 — engagedViews(2025-03 추가)가 최선 근사. 2025-03부터 `views`는 루프 재생 포함 |
| 평균 시청 비율 | `averageViewPercentage` | API가 계산해서 제공 |
| 평균 시청 시간 | `averageViewDuration` | 초 |
| 참여 | `likes`, `comments`, `shares` | |
| 구독 전환 | `subscribersGained` | |

구현: `ANALYTICS_METRICS`(sns/adapters/youtube/metrics.py) — 표준 metric_key([11-데이터모델](../plan/11-데이터모델.md) §4) ↔ API명 매핑 1지점.

## 3. 인스타 릴스 신호

**[공식]** (about.instagram.com "Instagram Ranking Explained" + Mosseri 2024-2026 반복 확인): ① **watch time**(1순위, 완주율·절대시간 모두) ② **sends per reach**("본 사람 중 몇 명이 친구에게 보냈나" — Mosseri 원문, **비팔로워 확산에 최중요**) ③ **likes per reach**(팔로워 도달). 댓글·저장은 2순위. 2024-04부터 신규 콘텐츠는 소규모 비팔로워 시드 테스트 → 성과 시 확산 **[공식]**.

**API 매핑** (Graph API media insights): `shares/reach`, `likes/reach`, `saved/reach`, `ig_reels_avg_watch_time`(ms), `views`, `reels_skip_rate`(개발 중). **주의 — 메트릭 개편 이력**: `impressions`(2024-07 폐기), `plays`(2025-04 완전 차단, `views`로 대체). 폴러는 메트릭명 매핑을 설정 1지점으로 두고 분기별 changelog 확인 필요. *(IG 폴러 구현은 adapters/instagram — 팀 분담)*

## 4. 통설로 분류 (신호 정의에서 배제)

가중치 배수("공유=좋아요 5배") · 최적 길이 30~90초 · 첫 1시간 확산 시간표 · 해시태그의 직접 랭킹 반영 · "70% 유지율=바이럴" 같은 임계값 — 전부 공식 근거 없음. 훅 "첫 1~3초"도 숫자 자체는 커뮤니티 관습(신호 구조상 방향은 타당).

## 5. 정책 리스크 — 자동 생성 콘텐츠 단속 (지배적 리스크)

- **[공식] YT 2025-07-15 "비진정성 콘텐츠"**(수익화 기준 개정): "이미지 슬라이드쇼, 템플릿화된 스토리라인", "대량 생산 인상을 주는 일반 템플릿 AI 콘텐츠"를 정조준 — **현 파이프라인의 산출물 프로필과 일치**. 생존 조건 = 영상별 고유한 내러티브·통찰, 템플릿 다변화. → **영상 퀄리티 개선(다음 브랜치)은 미관 문제가 아니라 정책 생존 요건.** 배포 자체보다 수익화(YPP)가 1차 관문이나, 대량 자동 업로드는 스팸 분류 리스크도 있음.
- **[공식] Meta 2025-07 AI 슬롭 단속**: 반복적·저노력 AI 콘텐츠 → 추천 배제 + 수익화 박탈(계정 단위). 계정 recommendability가 사실상 kill switch.
- AI 고지: 실사형 합성만 의무(양사) — 현 스타일(그래픽+TTS)은 의무 대상 아님. 단 실존 인물 음성 복제 금지.
- 기존 방어선과의 연결: C3 품질 게이트의 구조 유사도 검사(MAX_CONTENT_SIMILARITY)·FR-A2가 이 리스크의 1차 방어.

## 6. 신뢰성 검증 기준 — "이 신호 모델을 믿어도 되는가"

**검증 가능한 것은 "플랫폼 내부 가중치"가 아니라 "신호의 예측 유용성"이다.** 기준 4종:

| 검증 | 방법 | 통과 기준 |
|---|---|---|
| 예측 타당성 | 초기 창(6/24h) 신호 ↔ 최종 성과(72h+) 순위 상관 (FR-A3) | **게시물 ≥30건**에서 스피어만 상관 95% CI가 0 비포함. 미만은 "판정 불가" |
| 백테스트 | 초기 above 태그 게시물이 실제 최종 상위였는지 적중률 | 50%(우연) 대비 유의하게 높음 |
| 기준선 안정성 | 게시물 1건 증감에 태그가 뒤집히지 않는지 | 뒤집히면 표본 부족 — MIN_BASELINE_N(5) 상향 |
| 외부 정합성 | 계산한 engaged_rate ↔ 스튜디오 "시청 vs 스와이프" 수동 대조 | 방향 일치 (정의가 달라 절대값은 다를 수 있음) |

구조적 한계(설계에 반영됨): 개별 게시물 1건 결론은 불가능(동일 품질 10배 분산 — 분산 경고 상시 표기, FR-A4) / 플랫폼 변경 감지는 FR-A5(다계정 동시 체인지포인트)가 담당.

## 7. 지식의 유효기간

신호 **범주**(시청 선택률→유지율→참여→만족도 / watch time→sends→likes)는 2022~현재 다년 안정 **[공식 재확인 2025]** — 이 범주에만 코드를 걸었다. 가중치·임계값은 비공개·가변 — 그래서 절대 기준 없이 계정 자체 기준선 대비만 쓴다. API 표면은 IG가 특히 불안정(18개월 2회 파괴적 변경) — 매핑 dict 1지점 + 분기 changelog 확인.

## 출처 (발췌)

YT: support.google.com/youtube/answer/11914225 · answer/1311392(비진정성) · answer/14328491(AI 고지) · developers.google.com/youtube/analytics/metrics · blog.youtube(3분 쇼츠) / IG: about.instagram.com/blog/announcements/instagram-ranking-explained · developers.facebook.com/docs/instagram-api/reference/ig-media/insights · transparency.meta.com(AI 라벨) · Mosseri Threads(sends per reach, 2024) / 단속 보도: CNBC·Forbes 2025-07
