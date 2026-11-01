import { expect, it } from 'vitest'
import { render, screen } from '@testing-library/svelte'
import DecisionDay from './DecisionDay.svelte'

it('explains skipped days and separates all seven evidence keys from the decision', () => {
  render(DecisionDay, { decision: { id: 'd1', session: '2026-10-30', status: 'skipped', status_reason: '行情缺失', summary: '保留现金', evidence: { account: { cash: '1000' }, positions: [], plans: [], market: {}, recent_trades: [], pending_orders: [], constraints: {} }, holds: ['MU 继续持有'], orders: [] } })
  expect(screen.getByText('跳过原因：行情缺失')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Agent 看到的' })).toBeInTheDocument()
  expect(document.querySelectorAll('[data-evidence-key]')).toHaveLength(7)
  expect(screen.getByText('MU 继续持有')).toBeInTheDocument()
  expect(screen.getByText('保留现金')).toBeInTheDocument()
})
it('names the seven persisted evidence sections in Chinese', () => {
  render(DecisionDay, { decision: { id: 'd1', session: '2026-10-30', status: 'ok', status_reason: null, summary: '等待', evidence: { account: {}, positions: [], pending_orders: [], buyable: [], rejected_yesterday: [], recent_closed: [], market: {} }, orders: [] } })
  for (const name of ['账户与现金', '持仓', '在挂订单', '可买计划', '昨日被拒订单', '近期平仓', '市场环境']) expect(screen.getByRole('heading', { name })).toBeInTheDocument()
})
it('uses the existing Chinese explanation for a coded skipped reason', () => {
  render(DecisionDay, { decision: { id: 'd1', session: '2026-10-30', status: 'skipped', status_reason: 'stage_budget', summary: null } })
  expect(screen.getByText('跳过原因：超出时限')).toBeInTheDocument()
})
