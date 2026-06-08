-- ============================================================
-- tvad 편성현황 관리 테이블 (이 파일만 단독 실행)
-- ============================================================

-- 앱 설정 저장 테이블 (중복 그룹 등)
CREATE TABLE IF NOT EXISTS tvad_config (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE tvad_config ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "allow_all" ON tvad_config;
CREATE POLICY "allow_all" ON tvad_config
  FOR ALL TO anon
  USING (true)
  WITH CHECK (true);

-- 기본 중복 그룹 초기값
INSERT INTO tvad_config (key, value) VALUES (
  'restricted_groups',
  '[{"name":"A그룹","keywords":["MD선정추천","TV인기상품"]},{"name":"B그룹","keywords":["카테고리MD추천","카테고리베스트"]}]'
) ON CONFLICT (key) DO NOTHING;

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
