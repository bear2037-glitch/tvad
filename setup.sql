-- ============================================================
-- tvad 편성현황 관리 테이블 (이 파일만 단독 실행)
-- ============================================================

CREATE TABLE IF NOT EXISTS tvad_schedule (
  id            BIGSERIAL PRIMARY KEY,
  date_str      TEXT NOT NULL,
  mgmt_no       TEXT NOT NULL,
  m_code        TEXT DEFAULT '',
  item_name     TEXT DEFAULT '',
  main_copy     TEXT DEFAULT '',
  sub_copy      TEXT DEFAULT '',
  slot          TEXT NOT NULL,
  product_code  TEXT DEFAULT '',
  check_result  TEXT DEFAULT '',
  updated_at    TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT tvad_schedule_unique UNIQUE (date_str, mgmt_no, slot)
);

ALTER TABLE tvad_schedule ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "allow_all" ON tvad_schedule;
CREATE POLICY "allow_all" ON tvad_schedule
  FOR ALL TO anon
  USING (true)
  WITH CHECK (true);
