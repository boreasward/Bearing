-- 031: 五层与 Agent 角色（v2.12–v2.15 spec §11、§24）。十二张新表与旧 CHECK 的放宽，全部可重跑。
-- 真实库执行需单独授权：先 pg_dump 备份，再 psql --single-transaction -v ON_ERROR_STOP=1，
-- 再 roadmap import，最后重启服务。迁移不改旧行、不加种子数据。
-- 只追加：feature_flag_changes、sector_classifications、research_versions、roadmap_goals、
-- roadmap_versions、roadmap_decisions、roadmap_evidence、roadmap_briefs。
-- data_requests / roadmap_proposals 只许一次状态更新；portfolio_checks.commentary 只许一次补写。
-- research_requests 按 D51 可多次转移，应用层以 WHERE status = <期望前态> 守住状态机。

-- ---------------------------------------------------------------- §11.1 开关与数据层
CREATE TABLE IF NOT EXISTS feature_flag_changes (
  id             BIGSERIAL PRIMARY KEY,
  flag           TEXT CHECK (flag IN ('backup_sources', 'company_researcher', 'portfolio_manager', 'optimize_proposals')),
  enabled_before BOOLEAN,
  enabled_after  BOOLEAN NOT NULL,
  reason         TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 300),
  request_id     UUID UNIQUE NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS data_requests (
  id           BIGSERIAL PRIMARY KEY,
  run_id       TEXT,
  agent_run_id UUID REFERENCES agent_runs(id),
  family       TEXT NOT NULL,
  ticker       TEXT NOT NULL,
  requested_at TIMESTAMPTZ NOT NULL,
  status       TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'fetched', 'failed', 'deferred')),
  fetch_run_id BIGINT REFERENCES data_fetch_runs(id),
  rows         INT,
  reason       TEXT,
  rerun_run_id UUID,
  updated_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sector_classifications (
  id              BIGSERIAL PRIMARY KEY,
  ticker          TEXT NOT NULL,
  sic             TEXT,
  sic_description TEXT,
  "group"         TEXT,
  source          TEXT NOT NULL,
  fetched_at      TIMESTAMPTZ NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_sector_classifications_ticker ON sector_classifications (ticker, fetched_at DESC);

-- 放宽旧 CHECK：按定义找出 → DROP IF EXISTS → ADD（030 的写法，兼容旧约束名）。
DO $$
DECLARE cname text;
BEGIN
  FOR cname IN SELECT conname FROM pg_constraint
    WHERE conrelid = 'data_fetch_runs'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) ~ '\mfamily\M'
  LOOP EXECUTE format('ALTER TABLE data_fetch_runs DROP CONSTRAINT %I', cname); END LOOP;
END $$;
ALTER TABLE data_fetch_runs DROP CONSTRAINT IF EXISTS data_fetch_runs_family_check;
ALTER TABLE data_fetch_runs ADD CONSTRAINT data_fetch_runs_family_check
  CHECK (family IN ('bars', 'news', 'sec_filings', 'fred', 'universe', 'earnings', 'fundamentals', 'sec_text', 'sector'));

DO $$
DECLARE cname text;
BEGIN
  FOR cname IN SELECT conname FROM pg_constraint
    WHERE conrelid = 'data_fetch_runs'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) ~ '\mtrigger\M'
  LOOP EXECUTE format('ALTER TABLE data_fetch_runs DROP CONSTRAINT %I', cname); END LOOP;
END $$;
ALTER TABLE data_fetch_runs DROP CONSTRAINT IF EXISTS data_fetch_runs_trigger_check;
ALTER TABLE data_fetch_runs ADD CONSTRAINT data_fetch_runs_trigger_check
  CHECK (trigger IN ('scheduled', 'manual_batch', 'manual_family', 'manual_research', 'data_request'));

-- source 列已经存在；增加主源 / 备源枚举。news_items_source_check 不变。
DO $$
DECLARE cname text;
BEGIN
  FOR cname IN SELECT conname FROM pg_constraint
    WHERE conrelid = 'price_bars'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) ~ '\msource\M'
  LOOP EXECUTE format('ALTER TABLE price_bars DROP CONSTRAINT %I', cname); END LOOP;
END $$;
ALTER TABLE price_bars DROP CONSTRAINT IF EXISTS price_bars_source_check;
ALTER TABLE price_bars ADD CONSTRAINT price_bars_source_check CHECK (source IN ('yfinance', 'stooq'));

DO $$
DECLARE cname text;
BEGIN
  FOR cname IN SELECT conname FROM pg_constraint
    WHERE conrelid = 'earnings_dates'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) ~ '\msource\M'
  LOOP EXECUTE format('ALTER TABLE earnings_dates DROP CONSTRAINT %I', cname); END LOOP;
END $$;
ALTER TABLE earnings_dates DROP CONSTRAINT IF EXISTS earnings_dates_source_check;
ALTER TABLE earnings_dates ADD CONSTRAINT earnings_dates_source_check CHECK (source IN ('yfinance', 'nasdaq'));

-- ---------------------------------------------------------------- §11.2 Agent 与研究（D39 选 B：研究只出档案）
-- mode_plan_check、agent_plan_versions.origin、agent_attempt_resets.mode 保持原状。
DO $$
DECLARE cname text;
BEGIN
  FOR cname IN SELECT conname FROM pg_constraint
    WHERE conrelid = 'agent_runs'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) ~ '\mmode\M'
      AND pg_get_constraintdef(oid) !~ '\mplan_id\M'
      AND pg_get_constraintdef(oid) !~ '\mstatus\M'
      AND pg_get_constraintdef(oid) !~ '\mticker\M'
  LOOP EXECUTE format('ALTER TABLE agent_runs DROP CONSTRAINT %I', cname); END LOOP;
END $$;
ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS agent_runs_mode_check;
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_mode_check
  CHECK (mode IN ('propose', 'interpret', 'review', 'revise', 'retro', 'discuss', 'portfolio',
                  'research', 'pm', 'optimize', 'brief'));

DO $$
DECLARE cname text;
BEGIN
  FOR cname IN SELECT conname FROM pg_constraint
    WHERE conrelid = 'agent_runs'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) ~ '\mmode\M'
      AND pg_get_constraintdef(oid) ~ '\mstatus\M'
      AND pg_get_constraintdef(oid) !~ '\mplan_id\M'
  LOOP EXECUTE format('ALTER TABLE agent_runs DROP CONSTRAINT %I', cname); END LOOP;
END $$;
ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS agent_runs_interpret_no_pass_check;
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_interpret_no_pass_check
  CHECK (mode IN ('propose', 'revise', 'research') OR status <> 'pass');

DO $$
DECLARE cname text;
BEGIN
  FOR cname IN SELECT conname FROM pg_constraint
    WHERE conrelid = 'agent_runs'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) ~ '\mmode\M'
      AND pg_get_constraintdef(oid) ~ '\mticker\M'
  LOOP EXECUTE format('ALTER TABLE agent_runs DROP CONSTRAINT %I', cname); END LOOP;
END $$;
ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS agent_runs_portfolio_ticker_check;
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_portfolio_ticker_check
  CHECK ((mode IN ('portfolio', 'pm', 'optimize', 'brief')) = (ticker IS NULL));

DO $$
DECLARE cname text;
BEGIN
  FOR cname IN SELECT conname FROM pg_constraint
    WHERE conrelid = 'agent_tool_calls'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) ~ '\mphase\M'
  LOOP EXECUTE format('ALTER TABLE agent_tool_calls DROP CONSTRAINT %I', cname); END LOOP;
END $$;
ALTER TABLE agent_tool_calls DROP CONSTRAINT IF EXISTS agent_tool_calls_phase_check;
ALTER TABLE agent_tool_calls ADD CONSTRAINT agent_tool_calls_phase_check
  CHECK (phase IN ('propose', 'counter', 'review', 'retro', 'discuss', 'portfolio', 'research', 'pm', 'optimize', 'brief'));

CREATE TABLE IF NOT EXISTS research_requests (
  id            UUID PRIMARY KEY,
  ticker        TEXT NOT NULL,
  question      TEXT CHECK (length(question) <= 500),
  horizon_days  INT,
  source        TEXT NOT NULL CHECK (source IN ('manual')),
  status        TEXT NOT NULL CHECK (status IN ('queued', 'running', 'waiting_data', 'concluded', 'closed')),
  attempts      SMALLINT NOT NULL DEFAULT 0,
  request_id    UUID UNIQUE NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL,
  updated_at    TIMESTAMPTZ,
  closed_reason TEXT
);

CREATE TABLE IF NOT EXISTS research_versions (
  id           BIGSERIAL PRIMARY KEY,
  request_id   UUID NOT NULL REFERENCES research_requests(id),
  version_no   INT NOT NULL,
  agent_run_id UUID REFERENCES agent_runs(id),
  cutoff_at    TIMESTAMPTZ NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('concluded', 'waiting_data', 'error')),
  action       TEXT CHECK (action IN ('buy', 'pass', 'wait')),
  plan_id      UUID REFERENCES trade_plans(id),
  summary      TEXT,
  output       JSONB,
  review_when  JSONB,
  tokens       INT,
  elapsed_ms   INT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (request_id, version_no)
);

-- ---------------------------------------------------------------- §11.3 组合经理
CREATE TABLE IF NOT EXISTS portfolio_checks (
  id         BIGSERIAL PRIMARY KEY,
  account_id UUID NOT NULL REFERENCES accounts(id),
  session    DATE NOT NULL,
  run_id     TEXT NOT NULL,
  cutoff_at  TIMESTAMPTZ NOT NULL,
  version    TEXT NOT NULL,
  result     JSONB NOT NULL,
  commentary JSONB,
  pm_run_id  UUID REFERENCES agent_runs(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (account_id, session)
);

DO $$
DECLARE cname text;
BEGIN
  FOR cname IN SELECT conname FROM pg_constraint
    WHERE conrelid = 'stage_runs'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) ~ '\mstage\M'
  LOOP EXECUTE format('ALTER TABLE stage_runs DROP CONSTRAINT %I', cname); END LOOP;
END $$;
ALTER TABLE stage_runs DROP CONSTRAINT IF EXISTS stage_runs_stage_check;
ALTER TABLE stage_runs ADD CONSTRAINT stage_runs_stage_check
  CHECK (stage IN ('sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review',
                   'portfolio', 'accounts', 'push', 'retro', 'propose'));

-- ---------------------------------------------------------------- §11.4 roadmap 与提案
CREATE TABLE IF NOT EXISTS roadmap_goals (
  id         BIGSERIAL PRIMARY KEY,
  goal_id    TEXT NOT NULL,
  version    INT NOT NULL,
  supersedes BIGINT,
  body       TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (goal_id, version)
);

CREATE TABLE IF NOT EXISTS roadmap_versions (
  id              BIGSERIAL PRIMARY KEY,
  version_id      TEXT NOT NULL,
  version         INT NOT NULL,
  supersedes      BIGINT,
  title           TEXT NOT NULL,
  theme           TEXT,
  scope           TEXT,
  depends_on      TEXT[] NOT NULL DEFAULT '{}',
  status          TEXT NOT NULL CHECK (status IN ('planned', 'in_progress', 'engineering_done',
                                                  'user_acceptance_pending', 'done', 'dropped')),
  exit_conditions TEXT,
  spec_path       TEXT,
  plan_path       TEXT,
  tag             TEXT,
  proposal_id     BIGINT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (version_id, version)
);

CREATE TABLE IF NOT EXISTS roadmap_decisions (
  id          BIGSERIAL PRIMARY KEY,
  decision_no INT NOT NULL,
  version     INT NOT NULL,
  supersedes  BIGINT,
  decided_on  DATE NOT NULL,
  question    TEXT NOT NULL,
  decision    TEXT NOT NULL,
  quote       TEXT,
  version_ids TEXT[] NOT NULL DEFAULT '{}',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (decision_no, version)
);

CREATE TABLE IF NOT EXISTS roadmap_evidence (
  id          BIGSERIAL PRIMARY KEY,
  evidence_id TEXT NOT NULL,
  version     INT NOT NULL,
  version_id  TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('manual', 'scheduled', 'user_confirmed')),
  body        TEXT NOT NULL,
  observed_on DATE,
  source_path TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (evidence_id, version)
);

CREATE TABLE IF NOT EXISTS roadmap_proposals (
  id              BIGSERIAL PRIMARY KEY,
  run_id          TEXT,
  agent_run_id    UUID REFERENCES agent_runs(id),
  source          TEXT NOT NULL CHECK (source IN ('data', 'agent_quality', 'route')),
  title           TEXT NOT NULL,
  change          TEXT NOT NULL,
  rationale       TEXT NOT NULL,
  evidence_refs   TEXT[] NOT NULL,
  expected        TEXT,
  acceptance      TEXT,
  status          TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'approved', 'rejected', 'superseded')),
  decided_at      TIMESTAMPTZ,
  decision_reason TEXT,
  request_id      UUID UNIQUE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS roadmap_briefs (
  id           BIGSERIAL PRIMARY KEY,
  version_id   TEXT NOT NULL,
  proposal_id  BIGINT REFERENCES roadmap_proposals(id),
  agent_run_id UUID REFERENCES agent_runs(id),
  body         TEXT,
  status       TEXT NOT NULL CHECK (status IN ('ok', 'error')),
  reason       TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
