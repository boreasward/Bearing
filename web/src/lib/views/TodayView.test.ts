import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte'
import { tick } from 'svelte'
import TodayView from './TodayView.svelte'
import { invalidateAll } from '$lib/stores'
import { fixture } from './test-fixtures'
import type { MarketData } from '$lib/stores/types'
const marketData = fixture('get_market') as MarketData
const sections = [
 ['actions','今日操作','记下当天动作'], ['summary','一句话','摘要文字'], ['changes','和昨天比变了什么','变化文字'],
 ['market','市场环境','市场节文字'], ['holdings','持仓','持仓文字'], ['pending','待入场计划','计划文字'],
 ['research','研究','研究文字'], ['candidates','适合交易什么','名单文字'], ['account','账户','简报账户段'], ['unresolved','今天下不了结论的','结论文字'],
].map(([key,title,markdown]) => ({ key,title,markdown }))
const brief = (session = '2026-11-09', extra: Record<string, unknown> = {}) => ({ session, status:'final', markdown:'旧简报文本', sections, footer:'数据截至 2026-11-09 收盘。', market:{ ...marketData, session }, lead:null, error:null, unfinished:false, ...extra })
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers:{ 'content-type':'application/json' } })
function install(options: { list?: unknown; bySession?: Record<string, unknown>; today?: unknown; market?: unknown } = {}) {
 const list = options.list ?? { briefs:[{ session:'2026-11-09',status:'final' },{ session:'2026-11-06',status:'final' }] }
 const today = options.today ?? { session:'2026-11-09', cutoff_at:'2026-11-10T00:00:00Z', actions:[{ plan_id:1,ticker:'MU',kind:'sell_next_open',text:'下次开盘卖出',stop:'90',quantity:'20',reason:null }], account:{ cash:'1',equity:'2',unrealized_pnl:'0',realized_pnl:'0',cash_negative:false,notes:[] } }
 const route = vi.fn(async (input: RequestInfo | URL) => {
  const path = String(input)
  if (path === '/api/briefs?limit=30') return json(list, typeof list === 'number' ? list : 200)
  if (path === '/api/today') return json(today)
  if (path === '/api/market') return options.market === 404 ? json({ error:'no_run' },404) : json(options.market ?? marketData)
  const date = path.split('/').at(-1)!
  const value = options.bySession?.[date] ?? (date === 'latest' ? brief() : brief(date))
  if (value === 'no_run') return json({ error:'no_run' },404)
  if (value === 404) return json({ error:'no_brief' },404)
  if (value === 500) return json({ error:'brief_failed' },500)
  return json(value)
 })
 vi.stubGlobal('fetch', route)
 return route
}
beforeEach(() => invalidateAll())
afterEach(async () => { cleanup(); await tick(); vi.unstubAllGlobals() })
it('latest brief renders one card per section with account and market on the side', async () => {
 install(); const { container } = render(TodayView)
 const operations = await screen.findByRole('region', { name:'今日操作' })
 expect(screen.getByRole('region', { name:'一句话' })).toBeInTheDocument()
 expect(screen.getByRole('region', { name:'和昨天比变了什么' })).toBeInTheDocument()
 for (const name of ['持仓','待入场计划','研究','规则名单','今天下不了结论的']) expect(screen.getByRole('region', { name })).toBeInTheDocument()
 const aside = screen.getByRole('complementary', { name:'账户与行情' })
 expect(within(aside).getByRole('region', { name:'账户' })).toBeInTheDocument()
 expect(within(aside).getByRole('region', { name:'行情' })).toBeInTheDocument()
 const left = container.querySelector('.grid.min-w-0.gap-4')!
 expect(Array.from(left.querySelectorAll('[role="region"]')).map(region => region.getAttribute('aria-label'))).toEqual(['今日操作','一句话','和昨天比变了什么','持仓','待入场计划','研究','规则名单','今天下不了结论的'])
 expect(container.querySelector('aside')!.compareDocumentPosition(screen.getByRole('region', { name:'一句话' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
 expect(screen.queryByRole('region', { name:'市场环境' })).toBeNull(); expect(screen.queryByRole('region', { name:'适合交易什么' })).toBeNull()
 expect(screen.getAllByRole('region', { name:'账户' })).toHaveLength(1)
 expect(screen.queryByText('简报账户段')).toBeNull()
 expect(within(operations).getByRole('button', { name:'记成交' })).toBeInTheDocument()
 expect(screen.getByText('数据截至 2026-11-09 收盘。')).toBeInTheDocument()
 expect(screen.getByRole('region', { name:'账户' })).not.toHaveTextContent('最新')
})
it('a historical brief shows that day\'s text, no action buttons and a latest tag on the account', async () => {
 install({ bySession:{ '2026-11-06':brief('2026-11-06') } }); render(TodayView, { session:'2026-11-06' })
 const actions = await screen.findByRole('region', { name:'今日操作' })
 expect(actions).toHaveTextContent('记下当天动作'); expect(within(actions).queryByRole('button')).toBeNull()
 expect(screen.getByRole('region', { name:'账户' })).toHaveTextContent('最新')
 expect(screen.getByRole('region', { name:'行情' })).toHaveTextContent('2026-11-06')
})
it('historical actions use the empty reason when their section is absent', async () => {
 install({ bySession:{ '2026-11-06':brief('2026-11-06',{ sections:sections.filter(section => section.key !== 'actions') }) } }); render(TodayView,{session:'2026-11-06'})
 expect(await screen.findByRole('region',{name:'今日操作'})).toHaveTextContent('当天没有需要操作的事项')
})
it('a deep-linked session is historical until the list proves it is the newest', async () => {
 const fetcher = install({ list:500 }); render(TodayView,{session:'2026-11-06'})
 expect(await screen.findByRole('region',{name:'今日操作'})).not.toHaveTextContent('记成交')
 cleanup(); invalidateAll(); install(); render(TodayView,{session:'2026-11-09'})
 expect(await screen.findByRole('button',{name:'记成交'})).toBeInTheDocument()
 expect(fetcher).toBeDefined()
})
it('a dated link with no brief uses live actions and does not mark the account historical', async () => {
 install({ bySession:{'2026-11-05':404} }); render(TodayView,{session:'2026-11-05'})
 expect(await screen.findByRole('button',{name:'记成交'})).toBeInTheDocument()
 expect(screen.getByRole('region',{name:'账户'})).not.toHaveTextContent('最新')
 expect(screen.getByText('今天还没有简报')).toBeInTheDocument()
})
it('a dated link with no run uses live actions and does not mark the account historical', async () => {
 install({ bySession:{'2026-11-04':'no_run'} }); render(TodayView,{session:'2026-11-04'})
 expect(await screen.findByRole('button',{name:'记成交'})).toBeInTheDocument()
 expect(screen.getByRole('region',{name:'账户'})).not.toHaveTextContent('最新')
 expect(screen.getByText('还没有运行记录')).toBeInTheDocument()
})
it('a generic dated brief request failure remains historical and has no live actions', async () => {
 install({ bySession:{'2026-11-03':500} }); render(TodayView,{session:'2026-11-03'})
 expect(await screen.findByRole('region',{name:'简报'})).toBeInTheDocument()
 expect(screen.queryByRole('button',{name:'记成交'})).toBeNull()
 expect(screen.getByRole('region',{name:'账户'})).toHaveTextContent('最新')
})
it('the date selector sits in the page header and switches every dated card', async () => {
 install(); const { container } = render(TodayView)
 const selector = await screen.findByLabelText('选择简报')
 expect(selector.closest('[data-slot="page-header"]')).toBeTruthy()
 await fireEvent.click(selector); await fireEvent.pointerUp(screen.getByRole('option',{name:'2026-11-06'}))
 await waitFor(() => expect(screen.getByRole('region',{name:'一句话'})).toHaveTextContent('摘要文字'))
 const { getByText } = within(screen.getByRole('region',{name:'行情'})); await waitFor(() => expect(getByText(/2026-11-06/)).toBeInTheDocument())
 expect(container).toHaveTextContent('记下当天动作')
})
it('only shows the date selector when the list has multiple briefs', async () => {
 install({list:{briefs:[{session:'2026-11-09',status:'final'}]}}); render(TodayView)
 await screen.findByRole('region',{name:'一句话'}); expect(screen.queryByLabelText('选择简报')).toBeNull()
})
it('draft shows the unfinished alert and an error brief shows one reason only', async () => {
 install({ bySession:{ latest:brief('2026-11-09',{status:'draft',unfinished:true}) } }); const draft = render(TodayView)
 expect(await screen.findByText('二次确认未完成')).toBeInTheDocument(); draft.unmount(); invalidateAll()
 install({ bySession:{ latest:brief('2026-11-09',{status:'error',lead:'今天无法检查止损：no_bars',sections:[],market:null,error:'no_bars'}) } }); render(TodayView)
 expect(await screen.findByText('今天无法检查止损：no_bars')).toBeInTheDocument()
 expect(screen.getAllByText('今天无法检查止损：no_bars')).toHaveLength(1)
 expect(screen.queryByText('二次确认未完成')).toBeNull(); expect(screen.queryByRole('region',{name:'简报'})).toBeNull()
 expect(screen.getByRole('region',{name:'行情'})).toHaveTextContent('这一天没有市场数据')
})
it('market falls back to the section text when snapshot is absent', async () => {
 install({ bySession:{'2026-11-08':brief('2026-11-08',{market:null})} }); render(TodayView,{session:'2026-11-08'})
 expect(await screen.findByText('摘要文字')).toBeInTheDocument()
 expect(await within(screen.getByRole('region',{name:'行情'})).findByText('市场节文字')).toBeInTheDocument()
})
it('market card shows the empty reason when both snapshot and market section are absent', async () => {
 install({bySession:{'2026-11-07':brief('2026-11-07',{market:null,sections:sections.filter(section=>section.key!=='market')})}}); render(TodayView,{session:'2026-11-07'})
 expect(await screen.findByText('摘要文字')).toBeInTheDocument()
 expect(await within(screen.getByRole('region',{name:'行情'})).findByText('这一天没有市场数据')).toBeInTheDocument()
})
it('unknown section keys become a trailing text card', async () => {
 install({bySession:{latest:brief('2026-11-09',{sections:[...sections,{key:'outlook',title:'明日展望',markdown:'展望文字'}]})}}); render(TodayView)
 expect(await screen.findByRole('region',{name:'明日展望'})).toHaveTextContent('展望文字')
 const regions = screen.getAllByRole('region'); expect(regions.at(-1)).toHaveAttribute('aria-label','明日展望')
})
it('no brief keeps today\'s actions, the account and the latest market', async () => {
 install({bySession:{latest:404}}); render(TodayView)
 expect(await screen.findByRole('button',{name:'记成交'})).toBeInTheDocument()
 expect(screen.getByRole('region',{name:'账户'})).toBeInTheDocument(); expect(screen.getByText('今天还没有简报')).toBeInTheDocument()
 expect(screen.getByRole('region',{name:'行情'})).toHaveTextContent(marketData.session)
})
it('no brief and no market run show the server reason', async () => {
 install({bySession:{latest:404},market:404}); render(TodayView)
 expect(await screen.findByText('还没有运行记录')).toBeInTheDocument()
})
it('falls back to one card when the server sends no sections', async () => {
 install({bySession:{latest:{session:'2026-11-09',status:'final',markdown:'一段旧版简报',error:null,unfinished:false}}}); render(TodayView)
 expect(await screen.findByRole('region',{name:'简报'})).toHaveTextContent('一段旧版简报')
})
