import type { Account, AccountDetail, AccountPosition, CompareView, EquityRow } from '../../accounts'
import { REAL_ACCOUNT_ID } from '../../accounts'

export const equityFixture: EquityRow = {
  session: '2026-10-30', cash: '99000.0100', positions_value: '1100.00', equity: '100100.0100',
  realized_pnl_cum: '0', unrealized_pnl: '100.00', net_flows_cum: '0', day_pnl: '100.00', positions_count: 1,
  detail: {}, computed_at: '2026-10-31T02:00:00Z', account_version: 'account-1',
}
export const accountFixture: Account = {
  id: REAL_ACCOUNT_ID, kind: 'real', strategy: 'manual', name: '真实账户', opening_cash: '100000', opened_on: '2026-10-29',
  params: { risk_pct: '0.01', single_cap: '0.25', total_cap: '1', max_positions: 5 }, status: 'active',
  settled_through: '2026-10-30', latest: equityFixture, inception_return: '0.001',
  max_drawdown: { pct: '0.01', peak_session: '2026-10-29', trough_session: '2026-10-30' }, opened: true,
}
export const paperFixture: Account = { ...accountFixture, id: 'paper-1', kind: 'paper', strategy: 'agent_managed', name: 'Agent 账户' }
export const positionFixture: AccountPosition = {
  ticker: 'MU', quantity: '10', avg_cost: '100.0100', last_close: '110.00', market_value: '1100.00', unrealized_pnl: '99.9000',
  r_now: '0.5', stop: '95', stop_mode: 'initial', target1: '120', to_stop_pct: '-13.63', to_t1_pct: '9.09',
  held_sessions: 2, max_hold_sessions: 10, instruction: 'hold', reason: '维持计划',
  plan: { plan_id: 'p1', label: 'Agent MU 第 1 版', chain_signal_date: '2026-10-29', version_no: 1 },
}
export const detailFixture: AccountDetail = {
  account: accountFixture, equity: equityFixture, positions: [positionFixture], pending_orders: [],
  decision: null, reconciliation: [{ status: 'matched', ledger_equity: '100100.0100', snapshot_equity: '100100.0100' }],
  forms: { can_record_trades: true }, closed_positions: [],
}
export const compareFixture: CompareView = {
  columns: [{ account_id: accountFixture.id, name: accountFixture.name, strategy: 'manual' }, { account_id: paperFixture.id, name: paperFixture.name, strategy: 'agent_managed' }],
  rows: [
    { key: 'month_return', label: '本月收益率', values: [{ text: '+0.10%', value: '0.001' }, { text: '+0.20%', value: '0.002' }], spy: '+0.05%' },
    { key: 'mean_r', label: '平均 R', values: [{ text: null, value: null, note: '样本 < 5' }, { text: '+0.50R', value: '0.5' }], spy: null },
  ], cutoff_at: equityFixture.computed_at, calc_versions: ['account-1', 'paper-fill-1'],
}
