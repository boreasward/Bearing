-- 030: 账户与模拟交易（v2.11 spec docs/specs/2026-10-31-v2.11-accounts-and-paper-trading-design.md §7）。
-- 新建 accounts / account_param_changes / account_equity_days / paper_orders / paper_decisions / paper_chain_done；
-- trades、account_snapshots、cash_flows、holding_status 各加 account_id（默认真实账户，D2：旧行由默认值归入）；
-- 三处放宽：stage_runs.stage 加 'accounts'、agent_runs.mode 加 'portfolio'（ticker/signal_date/pool 转可空，
-- ticker 为空当且仅当 mode = 'portfolio'，D27）、agent_tool_calls.phase 加 'portfolio'。
-- 除 D2 的一行种子与四列默认值外不改任何旧行；唯一动到的旧约束是 holding_status 的唯一键
-- （(ticker, session) 换成 (account_id, ticker, session)，旧行都满足新键）。全部可重跑。
-- 真实库执行需单独授权（pg_dump 备份 + psql --single-transaction），且必须先于重启服务执行（spec D22）。

-- ---------------------------------------------------------------- §7.1 accounts、account_param_changes
-- 真实账户固定 id、kind/strategy 配对（真实=手动，模拟=Agent 自管或照计划执行）、初始现金与开户日同有同无。
CREATE TABLE IF NOT EXISTS accounts (
  id              UUID PRIMARY KEY,
  kind            TEXT NOT NULL CHECK (kind IN ('real', 'paper')),
  strategy        TEXT NOT NULL CHECK (strategy IN ('manual', 'agent_managed', 'follow_plans')),
  name            TEXT NOT NULL CHECK (btrim(name) <> ''),
  opening_cash    NUMERIC(18,6) CHECK (opening_cash IS NULL OR opening_cash > 0),
  opened_on       DATE,
  params          JSONB NOT NULL DEFAULT '{}',
  status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
  closed_at       TIMESTAMPTZ,
  close_reason    TEXT,
  settled_through DATE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (name),
  CHECK ((kind = 'real') = (strategy = 'manual')),
  CHECK (kind = 'real' OR opening_cash IS NOT NULL),
  CHECK ((opening_cash IS NULL) = (opened_on IS NULL)),
  CHECK ((status = 'closed') = (closed_at IS NOT NULL))
);
-- 全库恰好一个真实账户（spec §7.1）。
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_one_real ON accounts ((kind)) WHERE kind = 'real';
-- D2 的种子行：默认真实账户，初始现金与开户日为空（开账由 §8.1 的接口录，D3）。
INSERT INTO accounts (id, kind, strategy, name) VALUES ('00000000-0000-0000-0000-000000000001', 'real', 'manual',
                                                        '真实账户') ON CONFLICT (id) DO NOTHING;

-- 参数与开账的变更记录（§4.1：改动从下一交易日起生效，不重算历史）；只追加。
CREATE TABLE IF NOT EXISTS account_param_changes (
  id             BIGSERIAL PRIMARY KEY,
  account_id     UUID NOT NULL REFERENCES accounts(id),
  kind           TEXT NOT NULL CHECK (kind IN ('params', 'opening')),
  params_before  JSONB,
  params_after   JSONB,
  reason         TEXT,
  effective_from DATE NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_account_param_changes_account ON account_param_changes (account_id, created_at);

-- ---------------------------------------------------------------- §7.1 的三处放宽（D27、D13）
-- agent_runs：mode 加 'portfolio'；ticker / signal_date / pool 转可空，ticker 为空当且仅当 portfolio 运行。
-- 020 内联、026 / 027 具名的 mode CHECK：按定义找出只涉及 mode 的 CHECK 删掉，再加具名约束（同 026 – 028 的 DO 块）。
DO $$
DECLARE
  cname text;
BEGIN
  FOR cname IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'agent_runs'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) ~ '\mmode\M'
      AND pg_get_constraintdef(oid) !~ '\mplan_id\M'
      AND pg_get_constraintdef(oid) !~ '\mstatus\M'
  LOOP
    EXECUTE format('ALTER TABLE agent_runs DROP CONSTRAINT %I', cname);
  END LOOP;
END $$;
ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS agent_runs_mode_check;
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_mode_check
  CHECK (mode IN ('propose', 'interpret', 'review', 'revise', 'retro', 'discuss', 'portfolio'));
ALTER TABLE agent_runs ALTER COLUMN ticker DROP NOT NULL;
ALTER TABLE agent_runs ALTER COLUMN signal_date DROP NOT NULL;
ALTER TABLE agent_runs ALTER COLUMN pool DROP NOT NULL;
ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS agent_runs_portfolio_ticker_check;
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_portfolio_ticker_check
  CHECK ((mode = 'portfolio') = (ticker IS NULL));

-- agent_tool_calls.phase 加 'portfolio'（同 026 / 027 的做法）。
DO $$
DECLARE
  cname text;
BEGIN
  FOR cname IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'agent_tool_calls'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) ~ '\mphase\M'
  LOOP
    EXECUTE format('ALTER TABLE agent_tool_calls DROP CONSTRAINT %I', cname);
  END LOOP;
END $$;
ALTER TABLE agent_tool_calls DROP CONSTRAINT IF EXISTS agent_tool_calls_phase_check;
ALTER TABLE agent_tool_calls ADD CONSTRAINT agent_tool_calls_phase_check
  CHECK (phase IN ('propose', 'counter', 'review', 'retro', 'discuss', 'portfolio'));

-- stage_runs.stage 加 'accounts'（stages-5：review → accounts → push → retro，D13）。
DO $$
DECLARE
  cname text;
BEGIN
  FOR cname IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'stage_runs'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) ~ '\mstage\M'
  LOOP
    EXECUTE format('ALTER TABLE stage_runs DROP CONSTRAINT %I', cname);
  END LOOP;
END $$;
ALTER TABLE stage_runs DROP CONSTRAINT IF EXISTS stage_runs_stage_check;
ALTER TABLE stage_runs ADD CONSTRAINT stage_runs_stage_check
  CHECK (stage IN ('sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'accounts',
                   'push', 'retro'));

-- ---------------------------------------------------------------- §7.1 paper_chain_done（D24）
-- 每账户每链的消耗记录：买单作废（entry_voided）或止损 / 平仓出局（exited）后本链对本账户结束。
CREATE TABLE IF NOT EXISTS paper_chain_done (
  id                BIGSERIAL PRIMARY KEY,
  account_id        UUID NOT NULL REFERENCES accounts(id),
  ticker            TEXT NOT NULL,
  chain_signal_date DATE NOT NULL,
  reason            TEXT NOT NULL CHECK (reason IN ('entry_voided', 'exited')),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (account_id, ticker, chain_signal_date)
);

-- ---------------------------------------------------------------- §7.2 四张表加列
-- account_id 默认真实账户：旧行由默认值归入（D2）；只加列与索引，不改任何旧行。
ALTER TABLE trades ADD COLUMN IF NOT EXISTS account_id UUID NOT NULL
  DEFAULT '00000000-0000-0000-0000-000000000001' REFERENCES accounts(id);
ALTER TABLE account_snapshots ADD COLUMN IF NOT EXISTS account_id UUID NOT NULL
  DEFAULT '00000000-0000-0000-0000-000000000001' REFERENCES accounts(id);
ALTER TABLE cash_flows ADD COLUMN IF NOT EXISTS account_id UUID NOT NULL
  DEFAULT '00000000-0000-0000-0000-000000000001' REFERENCES accounts(id);
ALTER TABLE holding_status ADD COLUMN IF NOT EXISTS account_id UUID NOT NULL
  DEFAULT '00000000-0000-0000-0000-000000000001' REFERENCES accounts(id);
CREATE INDEX IF NOT EXISTS idx_trades_account ON trades (account_id, executed_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_account ON account_snapshots (account_id, as_of);
CREATE INDEX IF NOT EXISTS idx_flows_account ON cash_flows (account_id, flow_date);

-- trades 另加 recorded_by（模拟账户的成交由宿主写）与 detail（开账持仓、成交情形等）；旧行取默认值。
ALTER TABLE trades ADD COLUMN IF NOT EXISTS recorded_by TEXT NOT NULL DEFAULT 'user'
  CHECK (recorded_by IN ('user', 'host'));
ALTER TABLE trades ADD COLUMN IF NOT EXISTS detail JSONB NOT NULL DEFAULT '{}';

-- holding_status 的唯一键从 (ticker, session) 换成 (account_id, ticker, session)：029 的 UNIQUE 约束名由 PG 生成，
-- 按定义找出（不含 account_id 的那个）删掉（同 029 对价位 CHECK 的做法），旧行都满足新键。
DO $$
DECLARE
  cname text;
BEGIN
  FOR cname IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'holding_status'::regclass AND contype = 'u'
      AND pg_get_constraintdef(oid) LIKE '%ticker%'
      AND pg_get_constraintdef(oid) LIKE '%session%'
      AND pg_get_constraintdef(oid) NOT LIKE '%account_id%'
  LOOP
    EXECUTE format('ALTER TABLE holding_status DROP CONSTRAINT %I', cname);
  END LOOP;
END $$;
ALTER TABLE holding_status DROP CONSTRAINT IF EXISTS holding_status_ticker_session_key;
ALTER TABLE holding_status DROP CONSTRAINT IF EXISTS holding_status_account_ticker_session_key;
ALTER TABLE holding_status ADD CONSTRAINT holding_status_account_ticker_session_key
  UNIQUE (account_id, ticker, session);

-- ---------------------------------------------------------------- §7.3 request_hash 兼容
-- 应用层约定（ledger 的 trade_hash）：account_id 只在不是默认真实账户时进入哈希体，旧请求重放仍幂等；
-- 迁移本身不动 request_hash。

-- ---------------------------------------------------------------- §7.4 account_equity_days（只追加）
-- 每个账户每个交易日一行（同一天重算追加新行，读取每 (account_id, session) 取 computed_at 最新，D15）。
CREATE TABLE IF NOT EXISTS account_equity_days (
  id               BIGSERIAL PRIMARY KEY,
  account_id       UUID NOT NULL REFERENCES accounts(id),
  session          DATE NOT NULL,
  cash             NUMERIC(18,6) NOT NULL,
  positions_value  NUMERIC(18,6) NOT NULL,
  equity           NUMERIC(18,6) NOT NULL,
  realized_pnl_cum NUMERIC(18,6) NOT NULL,
  unrealized_pnl   NUMERIC(18,6) NOT NULL,
  net_flows_cum    NUMERIC(18,6) NOT NULL,
  day_pnl          NUMERIC(18,6),
  positions_count  INT NOT NULL,
  detail           JSONB NOT NULL DEFAULT '{}',
  account_version  TEXT NOT NULL,
  run_id           TEXT NOT NULL,
  computed_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_account_equity_days_account
  ON account_equity_days (account_id, session, computed_at DESC);

-- ---------------------------------------------------------------- §7.5 paper_orders
-- 一条买 / 卖 / 撤单意图的全生命周期：pending → filled | voided | expired | cancelled，或一开始就 rejected。
-- 状态列可更新（一次性迁移，updated_at 记录）；decision_id 的外键在 §7.6 建好 paper_decisions 后补。
CREATE TABLE IF NOT EXISTS paper_orders (
  id                UUID PRIMARY KEY,
  account_id        UUID NOT NULL REFERENCES accounts(id),
  decision_id       BIGINT,
  action            TEXT NOT NULL CHECK (action IN ('buy', 'sell', 'cancel')),
  ticker            TEXT NOT NULL,
  plan_id           UUID REFERENCES trade_plans(id),
  chain_signal_date DATE,
  shares            NUMERIC(18,6),
  portion           TEXT CHECK (portion IS NULL OR portion IN ('all', 'half')),
  target_order_id   UUID,
  placed_on         DATE NOT NULL,
  placed_by         TEXT NOT NULL CHECK (placed_by IN ('agent', 'host')),
  reason            TEXT,
  status            TEXT NOT NULL CHECK (status IN ('pending', 'filled', 'voided', 'expired', 'cancelled',
                                                    'rejected')),
  status_reason     TEXT,
  settled_on        DATE,
  trade_id          UUID REFERENCES trades(id),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT paper_orders_buy_plan_check CHECK (action <> 'buy' OR status = 'rejected' OR (plan_id IS NOT NULL AND chain_signal_date IS NOT NULL))
);
-- Unknown plan intent is auditable as rejected without inventing an FK target. Raw intent stays in decision.output.
-- Replace the draft's unnamed buy-plan check on reapplication; accepted orders still require a real plan and chain.
DO $$
DECLARE cname text;
BEGIN
  FOR cname IN SELECT conname FROM pg_constraint
    WHERE conrelid = 'paper_orders'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) ~ '\mchain_signal_date\M'
  LOOP EXECUTE format('ALTER TABLE paper_orders DROP CONSTRAINT %I', cname); END LOOP;
END $$;
ALTER TABLE paper_orders ADD CONSTRAINT paper_orders_buy_plan_check
  CHECK (action <> 'buy' OR status = 'rejected' OR (plan_id IS NOT NULL AND chain_signal_date IS NOT NULL));
-- CHECK 默认放过 NULL；明确要求表达式为 TRUE，并替换本地旧草案中可被 NULL 绕过的约束。
DO $$
DECLARE
  cname text;
BEGIN
  FOR cname IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'paper_orders'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) ~ '\mshares\M'
      AND pg_get_constraintdef(oid) ~ '\mportion\M'
  LOOP
    EXECUTE format('ALTER TABLE paper_orders DROP CONSTRAINT %I', cname);
  END LOOP;
END $$;
ALTER TABLE paper_orders ADD CONSTRAINT paper_orders_shares_check
  CHECK ((shares IS NOT NULL OR action = 'cancel' OR (action = 'sell' AND portion = 'half')) IS TRUE);
-- 同一账户同一条链（ticker + 信号日）至多一个在挂买单（D5 / §5.5）。
-- 重建同名索引也修复曾在本地执行的 030 草案（漏了 ticker，导致同日不同标的相互阻挡）。
DROP INDEX IF EXISTS idx_paper_orders_one_pending_buy;
CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_orders_one_pending_buy
  ON paper_orders (account_id, ticker, chain_signal_date) WHERE status = 'pending' AND action = 'buy';
CREATE INDEX IF NOT EXISTS idx_paper_orders_account ON paper_orders (account_id, created_at);

-- ---------------------------------------------------------------- §7.6 paper_decisions
-- 每个模拟账户每个交易日一行决策记录（同 session 重跑 ON CONFLICT DO NOTHING，D14）。
CREATE TABLE IF NOT EXISTS paper_decisions (
  id               BIGSERIAL PRIMARY KEY,
  account_id       UUID NOT NULL REFERENCES accounts(id),
  session          DATE NOT NULL,
  status           TEXT NOT NULL CHECK (status IN ('ok', 'skipped', 'rejected')),
  status_reason    TEXT,
  agent_run_id     UUID REFERENCES agent_runs(id),
  agent_version    TEXT,
  strategy_version TEXT,
  evidence         JSONB,
  output           JSONB,
  summary          TEXT,
  holds            JSONB,
  detail           JSONB,
  run_id           TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (account_id, session)
);
-- §7.5 留下的外键：paper_orders.decision_id → paper_decisions(id)（可重跑：已存在则跳过）。
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'paper_orders'::regclass
                 AND conname = 'paper_orders_decision_id_fkey') THEN
    ALTER TABLE paper_orders ADD CONSTRAINT paper_orders_decision_id_fkey
      FOREIGN KEY (decision_id) REFERENCES paper_decisions(id);
  END IF;
END $$;
