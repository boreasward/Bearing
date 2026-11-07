-- 034: v3.1 计划线、变化记录、持仓复查、出入金、作废、简报、对话、设置（spec §13）。
-- 可重跑：IF EXISTS / IF NOT EXISTS，约束（含本迁移自己加的具名约束）先 DROP IF EXISTS 再建。
ALTER TABLE v3_plans DROP CONSTRAINT IF EXISTS v3_plans_session_ticker_key;
ALTER TABLE v3_plans DROP CONSTRAINT IF EXISTS v3_plans_check;
ALTER TABLE v3_plans DROP CONSTRAINT IF EXISTS v3_plans_origin_check;
ALTER TABLE v3_plans ALTER COLUMN entry_low DROP NOT NULL, ALTER COLUMN entry_high DROP NOT NULL,
  ALTER COLUMN stop DROP NOT NULL, ALTER COLUMN target1 DROP NOT NULL;
ALTER TABLE v3_plans
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'planned',
  ADD COLUMN IF NOT EXISTS initial_stop NUMERIC(18,6),
  ADD COLUMN IF NOT EXISTS atr NUMERIC(18,6),
  ADD COLUMN IF NOT EXISTS "trailing" BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS confirmed_stop_order NUMERIC(18,6),
  ADD COLUMN IF NOT EXISTS start_session DATE,
  ADD COLUMN IF NOT EXISTS reviewed_through DATE,
  ADD COLUMN IF NOT EXISTS stop_hit_session DATE,
  ADD COLUMN IF NOT EXISTS target_hit_session DATE,
  ADD COLUMN IF NOT EXISTS sell_instruction TEXT,
  ADD COLUMN IF NOT EXISTS ended_session DATE,
  ADD COLUMN IF NOT EXISTS end_reason TEXT,
  ADD COLUMN IF NOT EXISTS outcome JSONB NOT NULL DEFAULT '{}';
UPDATE v3_plans SET status = 'expired', ended_session = valid_until, end_reason = 'migrated_034'
  WHERE status = 'pending' AND initial_stop IS NULL AND reviewed_through IS NULL;   -- 032 时代的行一律已过期（spec §13）
UPDATE v3_plans SET initial_stop = stop WHERE initial_stop IS NULL AND stop IS NOT NULL;
ALTER TABLE v3_plans DROP CONSTRAINT IF EXISTS v3_plans_status_check;
ALTER TABLE v3_plans DROP CONSTRAINT IF EXISTS v3_plans_kind_check;
ALTER TABLE v3_plans DROP CONSTRAINT IF EXISTS v3_plans_sell_instruction_check;
ALTER TABLE v3_plans DROP CONSTRAINT IF EXISTS v3_plans_levels_check;
ALTER TABLE v3_plans ADD CONSTRAINT v3_plans_status_check CHECK (status IN
  ('pending','open','closed','expired','invalidated','replaced','cancelled'));
ALTER TABLE v3_plans ADD CONSTRAINT v3_plans_kind_check CHECK (kind IN ('planned','adopted'));
ALTER TABLE v3_plans ADD CONSTRAINT v3_plans_origin_check CHECK (origin IN ('brief','chat','web','host'));
ALTER TABLE v3_plans ADD CONSTRAINT v3_plans_sell_instruction_check
  CHECK (sell_instruction IS NULL OR sell_instruction IN ('max_hold','thesis_broken'));
ALTER TABLE v3_plans ADD CONSTRAINT v3_plans_levels_check CHECK (
  (stop IS NULL AND entry_low IS NULL AND entry_high IS NULL AND target1 IS NULL)
  OR (stop IS NOT NULL AND entry_low IS NOT NULL AND entry_high IS NOT NULL AND target1 IS NOT NULL
      AND stop < entry_low AND entry_low <= entry_high AND entry_high < target1));
CREATE UNIQUE INDEX IF NOT EXISTS uq_v3_plans_one_active_per_ticker
  ON v3_plans (ticker) WHERE status IN ('pending','open');
CREATE INDEX IF NOT EXISTS idx_v3_plans_status ON v3_plans (status, created_at DESC);

ALTER TABLE v3_runs ADD COLUMN IF NOT EXISTS dropped JSONB NOT NULL DEFAULT '[]';

CREATE TABLE IF NOT EXISTS v3_plan_events (
  id BIGSERIAL PRIMARY KEY, plan_id BIGINT NOT NULL REFERENCES v3_plans(id), session DATE NOT NULL,
  event TEXT NOT NULL CHECK (event IN ('created','adopted','continued','revised','expired','invalidated',
    'replaced','cancelled','bought','added','sold','closed','stop_hit','target_hit','stop_raised',
    'stop_set','stop_order_confirmed','sell_instruction','split_adjusted','deviation')),
  reason TEXT NOT NULL, before JSONB NOT NULL DEFAULT '{}', after JSONB NOT NULL DEFAULT '{}',
  source TEXT NOT NULL CHECK (source IN ('host','agent','user')), refs JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
CREATE UNIQUE INDEX IF NOT EXISTS uq_v3_plan_events_host_once
  ON v3_plan_events (plan_id, session, event) WHERE source = 'host';
CREATE INDEX IF NOT EXISTS idx_v3_plan_events_session ON v3_plan_events (session);

CREATE TABLE IF NOT EXISTS v3_holding_reviews (
  id BIGSERIAL PRIMARY KEY, plan_id BIGINT NOT NULL REFERENCES v3_plans(id), session DATE NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('holds','broken','unknown')), reasons TEXT NOT NULL,
  counter TEXT NOT NULL DEFAULT '', refs JSONB NOT NULL DEFAULT '[]', agent_session TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (plan_id, session));

CREATE TABLE IF NOT EXISTS v3_cash_flows (
  id BIGSERIAL PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('opening','deposit','withdrawal','adjustment')),
  amount NUMERIC(18,2) NOT NULL CHECK (kind = 'adjustment' OR amount > 0),
  effective_at TIMESTAMPTZ NOT NULL, note TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());

CREATE TABLE IF NOT EXISTS v3_voids (
  id BIGSERIAL PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('trade','cash_flow')), target_id BIGINT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), UNIQUE (kind, target_id));

CREATE TABLE IF NOT EXISTS v3_briefs (
  id BIGSERIAL PRIMARY KEY, session DATE NOT NULL UNIQUE, run_id TEXT REFERENCES v3_runs(run_id),
  status TEXT NOT NULL CHECK (status IN ('draft','final','error')), error TEXT,
  draft_md TEXT NOT NULL DEFAULT '', final_md TEXT, summary TEXT, agenda JSONB NOT NULL DEFAULT '{}',
  runtime TEXT, provider TEXT, model TEXT, usage JSONB NOT NULL DEFAULT '{}', elapsed_seconds INT,
  pushed_error_at TIMESTAMPTZ, pushed_final_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());

CREATE TABLE IF NOT EXISTS v3_chats (
  id BIGSERIAL PRIMARY KEY, chat_id TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',
  runtime TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, read_only BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
CREATE TABLE IF NOT EXISTS v3_chat_messages (
  id BIGSERIAL PRIMARY KEY, chat_id TEXT NOT NULL REFERENCES v3_chats(chat_id), seq INT NOT NULL CHECK (seq >= 1),
  role TEXT NOT NULL CHECK (role IN ('user','assistant')), content TEXT NOT NULL, session DATE,
  agent_session TEXT, status TEXT NOT NULL CHECK (status IN ('ok','error','cancelled','timeout')),
  error TEXT, usage JSONB NOT NULL DEFAULT '{}', tool_refs JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), UNIQUE (chat_id, seq));

CREATE TABLE IF NOT EXISTS v3_settings (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
