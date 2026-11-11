import { afterEach, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/svelte'
import AccountCard from './AccountCard.svelte'
import { fixture, meta } from '../test-fixtures'
afterEach(cleanup)
it('renders get_account with provenance', () => {
 const { container } = render(AccountCard, { data: fixture('get_account'), meta })
 expect(container).toHaveTextContent('现金'); expect(container).toHaveTextContent('call:7')
})
it('preserves get_account and write-result snapshot content order', () => {
 const base = fixture('get_account') as Record<string, unknown> & { ledger: Record<string, unknown> }
 const flow = fixture('record_cash_flow') as { amount: string; effective_at: string }
 const getAccount = render(AccountCard, { data:base, meta })
 const accountText = getAccount.container.textContent ?? ''
 expect(accountText.indexOf('对账差额')).toBeLessThan(accountText.indexOf('AAPL'))
 expect(accountText.indexOf('AAPL')).toBeLessThan(accountText.indexOf('仓位快照来源：'))
 getAccount.unmount()
 const result = { ...base, amount:flow.amount, effective_at:flow.effective_at, equity:'11000.00', as_of:'2026-11-09', ledger:{ ...base.ledger, cash_negative:true, notes:['账本说明'] } }
 const writeResult = render(AccountCard, { data:result, meta })
 const text = writeResult.container.textContent ?? ''
 const order = ['对账差额','出入金：','对账总资产：','现金为负，请核对账本','账本说明','AAPL','仓位快照来源：','记出入金'].map(label => text.indexOf(label))
 expect(order.every(index => index >= 0)).toBe(true)
 expect(order).toEqual([...order].sort((a,b) => a-b))
})
it('explains missing values', () => {
 const { container } = render(AccountCard, { data: {}, meta })
 expect(container).toHaveTextContent('没算')
})
it('shows the exposure line from the get_account result and tolerates its absence', () => {
 const base = fixture('get_account') as Record<string, unknown>
 const withLine = render(AccountCard, { data:{ ...base, exposure:{ pct:'0.5500', cap:'0.6', room:'5000.00', incomplete:true } }, meta })
 expect(withLine.container).toHaveTextContent('仓位占比 55.0%（上限 60%），剩余额度 $5000.00')
 expect(withLine.container).toHaveTextContent('有持仓缺日线，占比偏低')
 withLine.unmount()
 const placeholder = render(AccountCard, { data:{ ...base, exposure:{ pct:null, cap:'0.6', room:null, incomplete:false } }, meta })
 expect(placeholder.container).toHaveTextContent('仓位占比 —（未提供账户资金）')
 placeholder.unmount()
 const { exposure: _drop, ...legacy } = base
 const old = render(AccountCard, { data:legacy, meta })
 expect(old.container).not.toHaveTextContent('仓位占比')
})
