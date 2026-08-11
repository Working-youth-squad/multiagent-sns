-- 스키마 v1 — docs/plan/11-데이터모델.md 기준 (15 테이블 중 schema_version은 러너가 생성)
-- 원칙: 정규화 + CHECK 물리 강제 + 지표 결측=NULL + append-only 이벤트
-- enum은 pg enum 타입 대신 CHECK 제약 — 값 추가가 마이그레이션 한 줄

CREATE TABLE channel (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    platform text NOT NULL CHECK (platform IN ('instagram', 'youtube')),
    handle text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'revoked')),
    -- 운영 모드 = 실험 변수 (FR-E1)
    mode text NOT NULL CHECK (mode IN ('auto', 'hybrid', 'off')),
    -- 토큰 평문 저장 금지 (NFR-7): 앱레벨 암호화 + 키 버전
    token_encrypted bytea,
    token_key_version integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (platform, handle)
);

CREATE TABLE cycle (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_ref text NOT NULL,
    status text NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'running', 'completed', 'failed')),
    -- Growth Agent 변형 선택 (루프 밖 결정론, FR-L3)
    decision jsonb,
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE topic (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title text NOT NULL,
    summary text,
    source text,
    status text NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'approved', 'rejected', 'used')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE topic_stats (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    topic_id uuid NOT NULL REFERENCES topic (id),
    format text NOT NULL CHECK (format IN ('feed_image', 'reels', 'shorts')),
    platform text NOT NULL CHECK (platform IN ('instagram', 'youtube')),
    trials integer NOT NULL DEFAULT 0,
    reward_sum double precision NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (topic_id, format, platform)
);

CREATE TABLE content_item (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cycle_id uuid NOT NULL REFERENCES cycle (id),
    topic_id uuid NOT NULL REFERENCES topic (id),
    format text NOT NULL CHECK (format IN ('feed_image', 'reels', 'shorts')),
    status text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'needs_review', 'approved', 'rejected')),
    -- 훅 분리 생성 기록 (FR-G5)
    hook_pattern text
        CHECK (hook_pattern IN ('bold_claim', 'curiosity', 'story', 'shock', 'question')),
    -- LLM 착지점 1/3 (FR-C4)
    body text,
    media_spec jsonb,
    -- hybrid에서 사람이 초안 수정 시 true (FR-E3)
    edited_by_human boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE media_asset (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    content_item_id uuid NOT NULL REFERENCES content_item (id),
    kind text NOT NULL CHECK (kind IN ('image', 'video', 'thumbnail', 'audio')),
    -- 저장소 벤더 교체 seam (FR-M3)
    storage_url text NOT NULL,
    checksum text NOT NULL,
    quality_status text NOT NULL DEFAULT 'needs_review'
        CHECK (quality_status IN ('passed', 'failed', 'needs_review')),
    quality_report jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE publication (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    content_item_id uuid NOT NULL REFERENCES content_item (id),
    channel_id uuid NOT NULL REFERENCES channel (id),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'published', 'failed', 'skipped')),
    external_post_id text,
    published_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    -- 교차 발행은 채널별 행 분리, 같은 채널 중복 발행 차단
    UNIQUE (content_item_id, channel_id)
);

CREATE TABLE publish_attempt (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- publication 1─1 publish_attempt (멱등 상태머신, FR-P3)
    publication_id uuid NOT NULL UNIQUE REFERENCES publication (id),
    state text NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'container_created', 'published', 'failed')),
    -- IG 2단계 발행의 컨테이너 ID 보존 (재시작 시 재사용)
    container_id text,
    error_class text
        CHECK (error_class IN ('auth', 'quota', 'spam_block', 'transient', 'permanent_unknown')),
    -- 미분류 오류 원문 보존 (FR-P4)
    error_raw text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE metric_observation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    publication_id uuid NOT NULL REFERENCES publication (id),
    -- 0=6h, 1=24h, 2=72h, 3+=이후 일 1회 (FR-A3 확정)
    window_index integer NOT NULL CHECK (window_index >= 0),
    observed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (publication_id, window_index)
);

CREATE TABLE metric_value (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    observation_id uuid NOT NULL REFERENCES metric_observation (id),
    -- metric_key 표준: docs/plan/11-데이터모델.md §4
    metric_key text NOT NULL,
    value double precision,
    missing boolean NOT NULL,
    -- 결측=NULL, 0으로 채우지 않음 (NFR-3)
    CONSTRAINT metric_missing_xor
        CHECK ((missing AND value IS NULL) OR (NOT missing AND value IS NOT NULL)),
    UNIQUE (observation_id, metric_key)
);

CREATE TABLE reward (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- publication 1─0..1 reward
    publication_id uuid NOT NULL UNIQUE REFERENCES publication (id),
    -- 미확정/표본 부족 = NULL → 학습 제외 (FR-L2)
    reward_value double precision,
    formula_version text,
    computed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE playbook (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope text NOT NULL CHECK (scope IN ('global', 'platform', 'format', 'topic')),
    scope_ref text,
    version integer NOT NULL,
    -- LLM 착지점 2/3 (FR-C4)
    guidance text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope, scope_ref, version)
);

CREATE TABLE analysis_note (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cycle_id uuid REFERENCES cycle (id),
    -- LLM 착지점 3/3 (FR-C4) — 수치 재계산 금지, 정직 귀인 (FR-L5)
    body text NOT NULL,
    insufficient_evidence boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- append-only (UPDATE/DELETE 금지는 코드 수준 규율)
CREATE TABLE run_event (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cycle_id uuid REFERENCES cycle (id),
    kind text NOT NULL CHECK (kind IN (
        'cycle_started', 'cycle_completed', 'agent_called', 'tool_called',
        'publish_attempted', 'metric_polled', 'error', 'cost', 'notice'
    )),
    payload jsonb,
    cost_usd numeric(10, 4),
    created_at timestamptz NOT NULL DEFAULT now()
);
