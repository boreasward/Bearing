-- 032: v3 agent-first 的七张事实表（v3 spec §15.6 第 0–1 步的地基）。
-- 全部 v3_ 前缀，与 v2 表互不影响；可重跑（IF NOT EXISTS）。
-- 只追加的表：v3_runs、v3_candidates、v3_trades、v3_equity、v3_tool_calls；
-- v3_verdicts 与 v3_plans 按 (session, ticker) 唯一，重复 upsert 覆盖并刷新 updated_at。

CREATE TABLE IF NOT EXISTS v3_runs (
  id BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, session DATE NOT NULL,
  trigger TEXT NOT NULL CHECK (trigger IN ('scheduled', 'manual_batch')),
  status TEXT NOT NULL CHECK (status IN ('ok', 'error')), error TEXT,
  market JSONB NOT NULL DEFAULT '{}', factors JSONB NOT NULL DEFAULT '{}',
  coverage JSONB NOT NULL DEFAULT '{}', holdings JSONB NOT NULL DEFAULT '[]',
  draft_md TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CHECK ((status = 'error') = (error IS NOT NULL)));
CREATE INDEX IF NOT EXISTS idx_v3_runs_session ON v3_runs (session, created_at DESC);

CREATE TABLE IF NOT EXISTS v3_candidates (
  id BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL REFERENCES v3_runs(run_id), session DATE NOT NULL,
  ticker TEXT NOT NULL, rank INT NOT NULL CHECK (rank >= 1),
  screen_group TEXT NOT NULL CHECK (screen_group IN ('trigger', 'setup')),
  trigger_rules JSONB NOT NULL DEFAULT '[]', rs DOUBLE PRECISION, atr_pct DOUBLE PRECISION,
  setup_tier TEXT NOT NULL, industry TEXT, industry_tier TEXT NOT NULL,
  reason TEXT NOT NULL, detail JSONB NOT NULL DEFAULT '{}',
  UNIQUE (run_id, ticker), UNIQUE (run_id, rank));

CREATE TABLE IF NOT EXISTS v3_verdicts (
  id BIGSERIAL PRIMARY KEY, session DATE NOT NULL, ticker TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('confirm', 'veto', 'unknown')),
  reasons TEXT NOT NULL, counter TEXT NOT NULL DEFAULT '', refs JSONB NOT NULL DEFAULT '[]',
  origin TEXT NOT NULL CHECK (origin IN ('brief', 'chat')), agent_session TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (session, ticker));

CREATE TABLE IF NOT EXISTS v3_plans (
  id BIGSERIAL PRIMARY KEY, session DATE NOT NULL, ticker TEXT NOT NULL,
  origin TEXT NOT NULL CHECK (origin IN ('brief', 'chat')),
  entry_type TEXT NOT NULL, entry_low NUMERIC(18,6) NOT NULL, entry_high NUMERIC(18,6) NOT NULL,
  stop NUMERIC(18,6) NOT NULL, target1 NUMERIC(18,6) NOT NULL,
  levels_source TEXT NOT NULL CHECK (levels_source IN ('host', 'agent')),
  max_hold_sessions INT NOT NULL, valid_from DATE NOT NULL, valid_until DATE NOT NULL,
  shares INT, sizing JSONB NOT NULL DEFAULT '{}', warnings JSONB NOT NULL DEFAULT '[]',
  thesis TEXT NOT NULL, invalidation TEXT NOT NULL, counter TEXT NOT NULL DEFAULT '',
  refs JSONB NOT NULL DEFAULT '[]', agent_session TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (session, ticker), CHECK (stop < entry_low AND entry_low <= entry_high AND entry_high < target1));

CREATE TABLE IF NOT EXISTS v3_trades (
  id BIGSERIAL PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, ticker TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('buy', 'sell')), quantity NUMERIC(18,6) NOT NULL CHECK (quantity > 0),
  price NUMERIC(18,6) NOT NULL CHECK (price > 0), fee NUMERIC(18,6) NOT NULL DEFAULT 0 CHECK (fee >= 0),
  executed_at TIMESTAMPTZ NOT NULL, plan_id BIGINT REFERENCES v3_plans(id), note TEXT NOT NULL DEFAULT '',
  voided_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());

CREATE TABLE IF NOT EXISTS v3_equity (
  id BIGSERIAL PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, as_of DATE NOT NULL,
  equity NUMERIC(18,2) NOT NULL CHECK (equity > 0), note TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());

CREATE TABLE IF NOT EXISTS v3_tool_calls (
  id BIGSERIAL PRIMARY KEY, agent_session TEXT NOT NULL, mode TEXT NOT NULL CHECK (mode IN ('brief', 'chat')),
  session DATE NOT NULL, seq INT NOT NULL CHECK (seq >= 1), tool TEXT NOT NULL,
  args JSONB NOT NULL DEFAULT '{}', result_sha256 TEXT, error TEXT,
  called_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), UNIQUE (agent_session, seq));
