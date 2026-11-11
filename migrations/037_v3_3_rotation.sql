-- 037: v3.3 换仓记录、计划上的换仓指令与资金来源、止损单数量待改（spec §11）。可重跑。
CREATE TABLE IF NOT EXISTS v3_rotations (
  id BIGSERIAL PRIMARY KEY, session DATE NOT NULL,
  origin TEXT NOT NULL CHECK (origin IN ('brief','chat')),
  candidate TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('hold','half','full')),
  holding_plan_id BIGINT REFERENCES v3_plans(id), holding_ticker TEXT,
  reasons TEXT NOT NULL, weakness TEXT NOT NULL DEFAULT '', counter TEXT NOT NULL DEFAULT '',
  cost_response TEXT NOT NULL DEFAULT '', refs JSONB NOT NULL DEFAULT '[]',
  snapshot JSONB NOT NULL, sell_quantity NUMERIC(18,6), buy_shares NUMERIC(18,6),
  agent_session TEXT NOT NULL, request_id TEXT UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
ALTER TABLE v3_rotations DROP CONSTRAINT IF EXISTS v3_rotations_half_full_check;
ALTER TABLE v3_rotations ADD CONSTRAINT v3_rotations_half_full_check CHECK (
  verdict = 'hold' OR (holding_plan_id IS NOT NULL AND holding_ticker IS NOT NULL
    AND sell_quantity IS NOT NULL AND sell_quantity > 0
    AND buy_shares IS NOT NULL AND buy_shares > 0
    AND weakness <> '' AND cost_response <> ''));
CREATE UNIQUE INDEX IF NOT EXISTS uq_v3_rotations_brief_once ON v3_rotations (session) WHERE origin = 'brief';
CREATE INDEX IF NOT EXISTS idx_v3_rotations_session ON v3_rotations (session DESC, id DESC);

ALTER TABLE v3_plans
  ADD COLUMN IF NOT EXISTS sell_quantity NUMERIC(18,6),
  ADD COLUMN IF NOT EXISTS rotation_id BIGINT REFERENCES v3_rotations(id),
  ADD COLUMN IF NOT EXISTS funded_by_rotation_id BIGINT REFERENCES v3_rotations(id),
  ADD COLUMN IF NOT EXISTS stop_quantity_stale BOOLEAN NOT NULL DEFAULT FALSE;
CREATE UNIQUE INDEX IF NOT EXISTS uq_v3_plans_funded_by_rotation
  ON v3_plans (funded_by_rotation_id) WHERE funded_by_rotation_id IS NOT NULL;
ALTER TABLE v3_plans DROP CONSTRAINT IF EXISTS v3_plans_sell_instruction_check;
ALTER TABLE v3_plans ADD CONSTRAINT v3_plans_sell_instruction_check
  CHECK (sell_instruction IS NULL OR sell_instruction IN ('max_hold','thesis_broken','rotation'));
ALTER TABLE v3_plans DROP CONSTRAINT IF EXISTS v3_plans_rotation_check;
ALTER TABLE v3_plans ADD CONSTRAINT v3_plans_rotation_check CHECK (
  (sell_instruction IS NOT DISTINCT FROM 'rotation') = (rotation_id IS NOT NULL)
  AND (rotation_id IS NULL) = (sell_quantity IS NULL));

ALTER TABLE v3_plan_events DROP CONSTRAINT IF EXISTS v3_plan_events_event_check;
ALTER TABLE v3_plan_events ADD CONSTRAINT v3_plan_events_event_check CHECK (event IN (
  'created','adopted','continued','revised','expired','invalidated','replaced','cancelled','bought','added',
  'sold','closed','stop_hit','target_hit','stop_raised','stop_set','stop_order_confirmed','sell_instruction',
  'split_adjusted','deviation',
  'rotation_instruction','rotation_withdrawn','rotation_superseded','stop_quantity_stale'));
