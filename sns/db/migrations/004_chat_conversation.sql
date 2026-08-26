-- 스키마 v4 — 키워드 챗봇 대화 (10-웹-알림 FR-W6 [신설] · FR-W5 시드 발행 경로)
-- 목적: 멀티턴 대화를 서버가 소유한다. 폼 POST 전체 새로고침 방식이라 화면은 매 턴
--       DB에서 대화 전량을 다시 읽어 그린다 — hidden input으로 이력을 실어 나르지
--       않는다(요청 크기가 턴마다 커지고, 클라이언트가 이력을 고칠 수 있다).
--
-- append-only: 메시지는 UPDATE·DELETE 대상이 아니다(run_event·channel_profile 규율과 동형).

CREATE TABLE chat_conversation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- 온보딩된 채널에 귀속시킬 수 있으면 귀속한다(프로필로 제외어·카테고리를 채우는
    -- 경로). 채널 없이 키워드만 탐색하는 사용도 1급이라 NULL 허용.
    channel_id uuid REFERENCES channel (id),
    title text,  -- 첫 사용자 발화에서 파생. 목록 화면용이라 없어도 동작한다.
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE chat_message (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES chat_conversation (id) ON DELETE CASCADE,
    -- role: 화면 좌우와 LLM 이력 복원을 동시에 가른다.
    --   user      = 사람 발화
    --   assistant = LLM 발화
    --   ranking   = rank_keywords 결과 박제. **LLM이 쓴 글이 아니다.**
    --   system    = 사이클 트리거·실패 등 앱이 남기는 사실 기록
    role text NOT NULL CHECK (role IN ('user', 'assistant', 'ranking', 'system')),
    -- 사람이 읽는 본문. role='ranking'이면 비어 있을 수 있다(표는 payload가 정본).
    body text NOT NULL DEFAULT '',
    -- role='ranking': ranking_to_dict() 산출 그대로. 반올림·요약하지 않는다 —
    -- rank_std=null을 0으로 바꾸면 "불일치 없음"으로 오독된다(sns.research.keywords).
    -- role='system': {"kind": ..., "cycle_id": ...} 등 사실 기록.
    payload jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- 유일한 읽기 패턴: 대화 1건의 메시지를 시간순 전량.
CREATE INDEX chat_message_thread_idx ON chat_message (conversation_id, created_at);

-- 목록 화면: 최근 대화부터.
CREATE INDEX chat_conversation_recent_idx ON chat_conversation (created_at DESC);
