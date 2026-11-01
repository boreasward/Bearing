import { afterEach, describe, expect, it, vi } from 'vitest'
import * as accounts from './accounts'
import { WorkbenchError } from './workbench'
import * as workbench from './workbench'

afterEach(() => vi.unstubAllGlobals())

describe('account API', () => {
  it('scopes the legacy real-account and review reads by account_id', async () => {
    const fetcher = vi.fn()
    vi.stubGlobal('fetch', fetcher)
    const run = async (call: () => Promise<unknown>, path: string) => {
      fetcher.mockResolvedValueOnce(new Response('{}'))
      await call()
      expect(fetcher.mock.lastCall?.[0]).toBe(path)
    }
    await run(() => workbench.getAccount('paper-1'), '/api/workbench/account/snapshots?account_id=paper-1')
    await run(() => workbench.getTrades('paper-1'), '/api/workbench/trades?account_id=paper-1')
    await run(() => workbench.getTradeReview(), '/api/workbench/review/trades')
    await run(() => workbench.getDiscipline('paper-1'), '/api/workbench/discipline?account_id=paper-1')
    await run(() => workbench.getTracks('2026-10-01', undefined, 'paper-1'), '/api/workbench/review/tracks?since=2026-10-01&account_id=paper-1')
  })
  it('uses account routes, encoded filters and unchanged Decimal strings', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ equity: '100000.0100' })))
    vi.stubGlobal('fetch', fetcher)
    const call = async (action: () => Promise<unknown>, url: string) => {
      fetcher.mockResolvedValue(new Response(JSON.stringify({ equity: '100000.0100' })))
      const result = await action()
      expect(fetcher.mock.lastCall?.[0]).toBe(url)
      expect(result).toEqual({ equity: '100000.0100' })
    }
    await call(() => accounts.listAccounts(true), '/api/accounts?include_closed=true')
    await call(() => accounts.getAccount('a/b'), '/api/accounts/a%2Fb')
    await call(() => accounts.getEquity('a', '2026-10-01', '2026-10-31'), '/api/accounts/a/equity?from=2026-10-01&to=2026-10-31')
    await call(() => accounts.getAccountTrades('a'), '/api/accounts/a/trades')
    await call(() => accounts.getOrders('a', 'pending'), '/api/accounts/a/orders?status=pending')
    await call(() => accounts.getDecisions('a'), '/api/accounts/a/decisions')
    await call(() => accounts.getDecision('a', '2026-10-30'), '/api/accounts/a/decisions?session=2026-10-30')
    await call(() => accounts.getCompare('2026-10-01'), '/api/accounts/compare?from=2026-10-01')
    expect(fetcher.mock.lastCall?.[1]).toMatchObject({ cache: 'no-store', headers: { Accept: 'application/json' } })
  })

  it('posts the exact mutation contract and retains request ids', async () => {
    const fetcher = vi.fn()
    vi.stubGlobal('fetch', fetcher)
    const bodies: [accounts.CreateAccountBody, accounts.OpeningBody, accounts.ChangeParamsBody, { reason: string }] = [
      { request_id: 'new-1', name: '计划账户', strategy: 'follow_plans' as const, opening_cash: '100000' },
      { request_id: 'open-1', opening_cash: '1000.10', opened_on: '2026-10-30', opening_positions: [{ ticker: 'MU', quantity: '2', cost_per_share: '100.10' }] },
      { request_id: 'params-1', params: { max_positions: 3 }, reason: '减少仓位' },
      { reason: '停止模拟' },
    ]
    for (const [index, url] of ['/api/accounts', '/api/accounts/a/opening', '/api/accounts/a/params', '/api/accounts/a/close'].entries()) {
      fetcher.mockResolvedValueOnce(new Response('{}'))
      if (index === 0) await accounts.createAccount(bodies[0])
      if (index === 1) await accounts.openAccount('a', bodies[1])
      if (index === 2) await accounts.changeParams('a', bodies[2])
      if (index === 3) await accounts.closeAccount('a', '停止模拟')
      expect(fetcher.mock.lastCall?.[0]).toBe(url)
      expect(fetcher.mock.lastCall?.[1]).toMatchObject({ method: 'POST', body: JSON.stringify(bodies[index]) })
    }
  })

  it('preserves typed failures and gives actionable Chinese account errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: 'migration_030_missing' }), { status: 503 })))
    await expect(accounts.listAccounts()).rejects.toBeInstanceOf(WorkbenchError)
    expect(accounts.describeAccountError(new WorkbenchError('migration_030_missing', 503))).toContain('迁移 030')
    expect(accounts.describeAccountError(new WorkbenchError('paper_account_is_host_managed', 400))).toContain('宿主')
    expect(accounts.describeAccountError(new WorkbenchError('already_opened', 409))).toContain('已开账')
  })
  it.each([
    { kind: 'trade', submit: () => workbench.postTrade({ account_id: accounts.REAL_ACCOUNT_ID, request_id: 'record-1', ticker: 'MU', side: 'buy', quantity: '5', price: '100', fee: '0', executed_at: '2026-10-28T13:30:00Z', note: null }) },
    { kind: 'flow', submit: () => workbench.postFlow({ account_id: accounts.REAL_ACCOUNT_ID, request_id: 'record-1', flow_date: '2026-10-28', amount: '1000' }) },
  ])('explains before-opening validation from the legacy $kind client used by the account page', async ({ submit }) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: 'record_before_opened_on' }), { status: 400 })))
    const failure = await submit().catch(error => error)
    expect(failure).toBeInstanceOf(WorkbenchError)
    expect(accounts.describeAccountError(failure)).toBe('成交或出入金日期不能早于开账日')
  })
})

describe('account display helpers', () => {
  it('translates order reasons by status and the account-only host_error skip reason', () => {
    expect(accounts.orderReasonText('cancelled', 'chain_ended')).toBe('计划链已结束，订单撤销')
    expect(accounts.orderReasonText('voided', 'gap_above_zone')).toBe('开盘高于入场区间，计划作废')
    expect(accounts.orderReasonText('rejected', 'max_positions')).toBe('达到持仓数量上限')
    expect(accounts.orderReasonText('cancelled', 'something_new')).toBe('something_new')
    expect(accounts.orderReasonText('pending', null)).toBe('')
    expect(accounts.decisionReasonText('host_error')).toBe('宿主处理出错')
  })
  it('keeps incomplete historical valuations as gaps and refuses an incomplete opening baseline', () => {
    const opening = { session: '2026-10-29', equity: '100000', detail: {} }
    const missing = { session: '2026-10-30', equity: '0', detail: { bars_missing: ['MU'] } }
    const result = accounts.assessEquityHistory([opening, missing], '2026-10-29')
    expect(result.data).toEqual([['2026-10-29', 100], ['2026-10-30', null]])
    expect(result.issues).toEqual([{ session: '2026-10-30', reason: '估值不完整：缺当日收盘 MU' }])
    const unusable = accounts.assessEquityHistory([missing, { ...opening, session: '2026-11-02' }], '2026-10-30')
    expect(unusable.data).toEqual([])
    expect(unusable.baselineWarning).toContain('2026-10-30')
    expect(unusable.baselineWarning).toContain('未绘制')
    expect(accounts.assessEquityHistory([opening], '2026-10-28').baselineWarning).toContain('缺少首个交易日')
  })
  it('refuses to replace a missing first trading day with the first available history row', () => {
    const result = accounts.assessEquityHistory([{ session: '2026-11-03', equity: '100000', detail: {} }], '2026-11-02')
    expect(result.data).toEqual([])
    expect(result.baselineWarning).toContain('缺少首个交易日 2026-11-02')
  })
  it('marks stale closes in every historical row and rejects invalid equity values', () => {
    const result = accounts.assessEquityHistory([
      { session: '2026-10-29', equity: '100000', detail: {} },
      { session: '2026-10-30', equity: '101000', detail: { stale: { MU: '2026-10-29' } } },
      { session: '2026-11-02', equity: 'unknown', detail: {} },
    ], '2026-10-29')
    expect(result.data).toEqual([['2026-10-29', 100], ['2026-10-30', null], ['2026-11-02', null]])
    expect(result.issues[0].reason).toContain('沿用旧收盘 MU（2026-10-29）')
    expect(result.issues[1].reason).toContain('权益数值不可用')
  })
  it('merges and deduplicates valid SPY points without relying on the first account', () => {
    const result = accounts.mergeSpyHistory([
      { spy: [] },
      { spy: [{ session: '2026-10-30', close: '500.00' }, { session: '2026-11-02', close: '510.00' }] },
      { spy: [{ session: '2026-10-29', close: '490.00' }, { session: '2026-10-30', close: '500.00' }, { session: '2026-11-03', close: '0' }] },
    ])
    expect(result).toEqual([{ session: '2026-10-29', close: '490.00' }, { session: '2026-10-30', close: '500.00' }, { session: '2026-11-02', close: '510.00' }])
  })
  it('normalizes each curve at its own first equity without changing source strings', () => {
    const rows = [{ session: '2026-10-29', equity: '100000.0100' }, { session: '2026-10-30', equity: '110000.0110' }]
    expect(accounts.normalizedEquity(rows)).toEqual([['2026-10-29', 100], ['2026-10-30', 110]])
    expect(rows[0].equity).toBe('100000.0100')
    expect(accounts.normalizedEquity([])).toEqual([])
    expect(accounts.normalizedEquity([{ session: '2026-10-30', equity: '0' }])).toEqual([])
  })
  it('uses a dash for missing numbers and explicit valuation degradation', () => {
    expect(accounts.decimalText('100.0100')).toBe('100.0100')
    expect(accounts.decimalText(null)).toBe('—')
    expect(accounts.valuationWarning({ bars_missing: ['MU'], stale: { MU: '2026-10-29' } })).toContain('MU')
    expect(accounts.valuationWarning({})).toBe('')
  })
})
