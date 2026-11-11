import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/svelte'
import RotationRetro from './RotationRetro.svelte'
import type { RotationGroup, RotationRecord, RotationRetroData } from './types'

afterEach(cleanup)

const NOT_ENOUGH: RotationGroup = { sample: 2, enough: false, b_win_rate: null, mean_diff: null }

const SWITCHED: RotationRecord = {
  rotation_id: 3,
  session: '2026-11-04',
  origin: 'brief',
  candidate: 'BBB',
  verdict: 'half',
  verdict_text: '半仓换',
  holding_ticker: 'AAA',
  reasons: 'BBB 的相对强度与行业都更强',
  weakness: 'AAA 放量不足',
  sell_quantity: '150',
  buy_shares: '217',
  fee_estimate: '2.00',
  execution: { state: 'done', sold_quantity: '150', sold_session: '2026-11-05', bought: true },
  compare: [
    { horizon: 5, status: 'done', a_pct: '0.0100', b_pct: '0.0450', diff: '0.0350' },
    { horizon: 10, status: 'done', a_pct: '0.0200', b_pct: '0.0100', diff: '-0.0100' },
    { horizon: 20, status: 'unfinished', a_pct: null, b_pct: null, diff: null },
  ],
}

const HELD: RotationRecord = {
  rotation_id: 4,
  session: '2026-11-05',
  origin: 'chat',
  candidate: 'FFF',
  verdict: 'hold',
  verdict_text: '继续持有',
  holding_ticker: null,
  reasons: '没有更强的候选',
  weakness: '',
  sell_quantity: null,
  buy_shares: null,
  fee_estimate: '0.00',
  execution: null,
  compare: null,
}

const data = (patch: Partial<RotationRetroData> = {}): RotationRetroData => ({
  records: [SWITCHED, HELD],
  summary: {
    counts: { hold: 1, half: 1, full: 0 },
    executed: 1,
    fee_total: '2.00',
    switched: NOT_ENOUGH,
    held: { sample: 0, enough: false, b_win_rate: null, mean_diff: null },
  },
  min_sample: 10,
  ...patch,
})

describe('RotationRetro', () => {
  it('开头一句写明规则未经验证', () => {
    render(RotationRetro, { data: data() })
    expect(screen.getByRole('region', { name: '换仓比较' })).toHaveTextContent('规则未经验证；样本不足的组不给数')
  })

  it('每条记录一行：日期、B、A、结论、执行情况、三个时点的差值', () => {
    render(RotationRetro, { data: data() })
    const rows = screen.getAllByRole('row')
    expect(rows).toHaveLength(3) // 表头 + 两条记录
    const first = rows[1]
    expect(first).toHaveTextContent('2026-11-04')
    expect(first).toHaveTextContent('BBB')
    expect(first).toHaveTextContent('AAA')
    expect(within(first).getByText('半仓换')).toBeInTheDocument()
    expect(within(first).getByText('已执行')).toBeInTheDocument()
    expect(first).toHaveTextContent('卖出 150 股')
    expect(first).toHaveTextContent('BBB 的相对强度与行业都更强') // 理由摘要（spec §9）
    expect(first).toHaveTextContent('+3.50%')
    expect(first).toHaveTextContent('-1.00%')
  })

  it('日线不够的时点显示「进行中」', () => {
    render(RotationRetro, { data: data() })
    expect(screen.getAllByRole('row')[1]).toHaveTextContent('进行中')
  })

  it('继续持有且没指明持仓：对照列为空', () => {
    render(RotationRetro, { data: data() })
    const held = screen.getAllByRole('row')[2]
    expect(held).toHaveTextContent('FFF')
    expect(within(held).getByText('继续持有')).toBeInTheDocument()
    expect(held).not.toHaveTextContent('进行中')
    expect(held).not.toHaveTextContent('%')
    // 继续持有没有执行情况
    expect(within(held).queryByText('已执行')).toBeNull()
    expect(within(held).queryByText('待执行')).toBeNull()
  })

  it('汇总：样本不足时只显示次数与「样本不足」', () => {
    render(RotationRetro, { data: data() })
    const summary = screen.getByRole('group', { name: '换仓汇总' })
    expect(summary).toHaveTextContent('继续持有 1 次')
    expect(summary).toHaveTextContent('半仓换 1 次')
    expect(summary).toHaveTextContent('全仓换 0 次')
    expect(summary).toHaveTextContent('已执行 1 次')
    expect(summary).toHaveTextContent('手续费估计合计 $2.00')
    expect(summary).toHaveTextContent('换仓（半仓与全仓）：2 次')
    expect(within(summary).getAllByText('样本不足').length).toBeGreaterThan(0)
    expect(summary).not.toHaveTextContent('跑赢')
  })

  it('汇总：样本够时显示跑赢占比与平均差值', () => {
    const base = data()
    render(RotationRetro, {
      data: {
        ...base,
        summary: {
          ...base.summary,
          switched: { sample: 12, enough: true, b_win_rate: '0.5833', mean_diff: '0.0125' },
          held: { sample: 10, enough: true, b_win_rate: '0.3000', mean_diff: '-0.0200' },
        },
      },
    })
    const summary = screen.getByRole('group', { name: '换仓汇总' })
    expect(summary).toHaveTextContent('换仓（半仓与全仓）：12 次 · 20 日后 B 跑赢 A 占比 58.3% · 平均差值 +1.25%')
    expect(summary).toHaveTextContent('继续持有：10 次 · 20 日后 B 跑赢 A 占比 30.0% · 平均差值 -2.00%')
    expect(summary).not.toHaveTextContent('样本不足')
  })

  it('没有记录时显示「还没有换仓比较」', () => {
    render(RotationRetro, {
      data: data({ records: [], summary: { counts: { hold: 0, half: 0, full: 0 }, executed: 0, fee_total: '0.00', switched: { ...NOT_ENOUGH, sample: 0 }, held: { ...NOT_ENOUGH, sample: 0 } } }),
    })
    expect(screen.getByText('还没有换仓比较')).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })
})
