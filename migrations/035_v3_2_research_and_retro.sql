-- 035: v3.2 研究记录与变化记录、计划的研究关联、简报行的周报列（spec §11）。
CREATE TABLE IF NOT EXISTS v3_research (
  id BIGSERIAL PRIMARY KEY, ticker TEXT NOT NULL, thesis TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'watching'
    CHECK (status IN ('watching','planned','rejected','expired','dropped')),
  origin TEXT NOT NULL CHECK (origin IN ('chat','web')),
  created_session DATE NOT NULL, valid_until DATE NOT NULL,
  condition JSONB, condition_text TEXT,
  verdict TEXT CHECK (verdict IS NULL OR verdict IN ('ready','watch','reject')),
  reasons TEXT NOT NULL DEFAULT '', counter TEXT NOT NULL DEFAULT '', refs JSONB NOT NULL DEFAULT '[]',
  agent_session TEXT, checked_through DATE, triggered_session DATE,
  plan_id BIGINT REFERENCES v3_plans(id), ended_session DATE, end_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
CREATE UNIQUE INDEX IF NOT EXISTS uq_v3_research_one_watching_per_ticker
  ON v3_research (ticker) WHERE status = 'watching';
CREATE INDEX IF NOT EXISTS idx_v3_research_status ON v3_research (status, created_at DESC);

CREATE TABLE IF NOT EXISTS v3_research_events (
  id BIGSERIAL PRIMARY KEY, research_id BIGINT NOT NULL REFERENCES v3_research(id), session DATE NOT NULL,
  event TEXT NOT NULL CHECK (event IN ('created','concluded','triggered','planned','rejected','expired','dropped')),
  reason TEXT NOT NULL, before JSONB NOT NULL DEFAULT '{}', after JSONB NOT NULL DEFAULT '{}',
  source TEXT NOT NULL CHECK (source IN ('host','agent','user')), refs JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
CREATE UNIQUE INDEX IF NOT EXISTS uq_v3_research_events_host_once
  ON v3_research_events (research_id, session, event) WHERE source = 'host';
CREATE INDEX IF NOT EXISTS idx_v3_research_events_session ON v3_research_events (session);

ALTER TABLE v3_plans ADD COLUMN IF NOT EXISTS research_id BIGINT REFERENCES v3_research(id);
ALTER TABLE v3_briefs ADD COLUMN IF NOT EXISTS weekly_md TEXT,
  ADD COLUMN IF NOT EXISTS pushed_weekly_at TIMESTAMPTZ;
