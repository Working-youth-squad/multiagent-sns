-- 발행 모드 3분류(수동 manual · 반자동 hybrid · 자동 auto) 제거 — 002를 되돌린다.

-- 남는 경로는 하나다: AI 생성 → 품질 게이트 → 기계 발행. 사람 개입 지점 없음.

-- 되돌림은 forward migration(drop/alter)으로 처리한다(11-데이터모델.md §6).



-- 1) 발행 시점 모드 스냅샷 제거(002 §2). 비교할 모드가 없으면 증빙도 필요 없다.

ALTER TABLE publication DROP COLUMN mode;



-- 2) 채널 운영 모드 제거(001 §channel, 002 §1). 채널 발행 중지는 이미

--    channel.status(active/paused/revoked)가 담당하므로 'off'도 함께 사라진다.

ALTER TABLE channel DROP COLUMN mode;



-- 3) hybrid 사람 개입 기록 제거(001, FR-E3).

ALTER TABLE content_item DROP COLUMN edited_by_human;



-- 4) 사람 승인 관문 제거 → content_item.status에서 needs_review 소거.

--    게이트에 걸려 보류돼 있던 초안은 rejected로 종결한다 — 승인할 사람이 없다.

UPDATE content_item SET status = 'rejected' WHERE status = 'needs_review';

ALTER TABLE content_item DROP CONSTRAINT content_item_status_check;

ALTER TABLE content_item ADD CONSTRAINT content_item_status_check

    CHECK (status IN ('draft', 'approved', 'rejected'));



-- 5) 품질 게이트는 passed/failed만 낸다. 기본값을 fail-closed로 —

--    게이트가 passed를 쓰기 전까지 어떤 자산도 발행에 진입하지 못한다.

UPDATE media_asset SET quality_status = 'failed' WHERE quality_status = 'needs_review';

ALTER TABLE media_asset ALTER COLUMN quality_status SET DEFAULT 'failed';

ALTER TABLE media_asset DROP CONSTRAINT media_asset_quality_status_check;

ALTER TABLE media_asset ADD CONSTRAINT media_asset_quality_status_check

    CHECK (quality_status IN ('passed', 'failed'));



-- 002의 부분 유니크 인덱스(publication_channel_external_post_key)는 남긴다:

-- 수동 등록 멱등을 위해 들어왔지만, 같은 채널에 같은 외부 게시물이 두 번 기록되는

-- 것을 막는 방어는 기계 발행에도 그대로 유효하다.

