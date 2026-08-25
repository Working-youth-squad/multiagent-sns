-- 스키마 v3 — 온보딩 사전 인터뷰 결과 = 채널 프로필 (FR-W2 구체화)
-- 목적: 인터뷰(주제·컨셉·목표·캐릭터)로 확정한 계정 성격을 채널에 귀속시킨다.
--       FR-W2 "설정 변경 = 새 국면, 이력 보존" — 개정은 UPDATE가 아니라 새 행이다
--       (append-only, run_event 규율과 동형). 현재 프로필 = 채널별 최신 행.
--       기존 실험 채널은 행이 없고(프로필 없음) 동작이 바뀌지 않는다.
CREATE TABLE channel_profile (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id uuid NOT NULL REFERENCES channel (id),
    -- 인터뷰 산출 전체(구조는 sns.onboarding.profile이 검증·정본화). jsonb 한 칸인
    -- 이유: 조인 대상이 아니고 항목이 인터뷰 개정마다 유동적이다.
    profile jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- 채널별 최신 행 조회(latest_profile)가 유일한 읽기 패턴.
CREATE INDEX channel_profile_latest_idx ON channel_profile (channel_id, created_at DESC);
