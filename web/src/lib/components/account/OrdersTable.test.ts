import { expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/svelte'
import type { PaperOrder } from '../../accounts'
import OrdersTable from './OrdersTable.svelte'

it('shows the host rejection, original reason and next-session result', () => {
  render(OrdersTable, { orders: [{ id: 'o1', ticker: 'MU', action: 'buy', shares: '10', status: 'rejected', reason: '等待回踩', rejection_code: 'insufficient_cash', result_text: '次日未成交' }] })
  const table = screen.getByRole('table', { name: '模拟订单' })
  expect(table).toHaveTextContent('现金不足')
  expect(table).toHaveTextContent('等待回踩')
  expect(table).toHaveTextContent('次日未成交')
  expect(table).toHaveTextContent('未通过校验')
})
it('reads the persisted API status_reason as a host rejection code', () => {
  render(OrdersTable, { orders: [{ id: 'o1', ticker: 'MU', action: 'buy', shares: '10', status: 'rejected', status_reason: 'max_positions', reason: '选择当前计划' }] })
  const row = screen.getByRole('table', { name: '模拟订单' })
  expect(row).toHaveTextContent('达到持仓数量上限')
})

it('shows the expected five shares for a pending half sale of a ten-share holding', () => {
  const order: PaperOrder = { id: 'half-pending', ticker: 'MU', action: 'sell', portion: 'half', status: 'pending', shares: '5', quantity: '10', filled_quantity: null }
  render(OrdersTable, { orders: [order] })
  const table = within(screen.getByRole('table', { name: '模拟订单' }))
  expect(table.getByRole('cell', { name: '卖出一半' })).toBeInTheDocument()
  expect(table.getByRole('cell', { name: '5' })).toBeInTheDocument()
  expect(table.queryByRole('cell', { name: '10' })).toBeNull()
})

it('prefers the actual ten-share fill after a split and keeps the half-sale intent', () => {
  const order: PaperOrder = { id: 'half-filled', ticker: 'MU', action: 'sell', portion: 'half', status: 'filled', shares: '5', filled_quantity: '10.0000' }
  render(OrdersTable, { orders: [order] })
  const table = within(screen.getByRole('table', { name: '模拟订单' }))
  expect(table.getByRole('cell', { name: '卖出一半' })).toBeInTheDocument()
  expect(table.getByRole('cell', { name: '10.0000' })).toBeInTheDocument()
  expect(table.queryByRole('cell', { name: '5' })).toBeNull()
})

it.each([
  { field: 'shares', quantity: { shares: '5.0000' } },
  { field: 'quantity', quantity: { quantity: '5.0000' } },
])('keeps the legacy $field fallback when actual fill quantity is absent', ({ quantity }) => {
  const order: PaperOrder = { id: 'legacy-filled', ticker: 'MU', action: 'sell', portion: 'half', status: 'filled', ...quantity }
  render(OrdersTable, { orders: [order] })
  const table = within(screen.getByRole('table', { name: '模拟订单' }))
  expect(table.getByRole('cell', { name: '5.0000' })).toBeInTheDocument()
  expect(table.getByRole('cell', { name: '卖出一半' })).toBeInTheDocument()
})

it.each([
  { status: 'cancelled', status_reason: 'agent_cancel', text: '已撤单：Agent 主动撤单' },
  { status: 'expired', status_reason: 'validity_expired', text: '已过期：计划有效期已过' },
  { status: 'voided', status_reason: 'gap_below_stop', text: '已作废：开盘不高于止损，计划作废' },
] as const)('translates $status_reason when the API sent no result text', ({ status, status_reason, text }) => {
  render(OrdersTable, { orders: [{ id: 'o1', ticker: 'MU', action: 'buy', shares: '10', status, status_reason }] })
  expect(screen.getByRole('table', { name: '模拟订单' })).toHaveTextContent(text)
})
