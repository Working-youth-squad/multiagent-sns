-- 스키마 v2 — 발행 모드 3분류(수동 manual · 반자동 hybrid · 자동 auto), FR-E1/E5
-- 목적: "같은 프롬프트(주제)로 어느 모드가 가장 효과적인가" 비교의 증빙을
--       발행 행 자체에 남긴다. channel.mode는 바뀔 수 있으므로 publication에
--       발행 시점 모드를 스냅샷한다(mode) — 비교 리포트의 단일 출처.

-- 1) 채널 운영 모드에 manual 추가 (001의 인라인 CHECK 이름은 channel_mode_check)
ALTER TABLE channel DROP CONSTRAINT channel_mode_check;
ALTER TABLE channel ADD CONSTRAINT channel_mode_check
    CHECK (mode IN ('auto', 'hybrid', 'manual', 'off'));

-- 2) 발행 행에 모드 스냅샷. NULL = 분류 도입 이전 레거시 행(증빙 불가로 정직 표기).
ALTER TABLE publication ADD COLUMN mode text
    CHECK (mode IN ('auto', 'hybrid', 'manual'));
UPDATE publication p
   SET mode = ch.mode
  FROM channel ch
 WHERE ch.id = p.channel_id
   AND ch.mode IN ('auto', 'hybrid', 'manual');

-- 3) 수동 등록 멱등 경계: 같은 채널에 같은 외부 게시물을 두 번 등록하지 못한다.
--    (기계 발행에도 무해 — 외부 post id는 채널 내에서 유일하다.)
CREATE UNIQUE INDEX publication_channel_external_post_key
    ON publication (channel_id, external_post_id)
 WHERE external_post_id IS NOT NULL;
