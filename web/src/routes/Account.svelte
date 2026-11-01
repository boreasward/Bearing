<script lang="ts">
  import { onDestroy, untrack } from 'svelte'
  import type { EChartsOption } from 'echarts'
  import * as ac from '$lib/accounts'
  import { REAL_ACCOUNT_ID } from '$lib/accounts'
  import { fmtBeijing } from '$lib/format'
  import * as wb from '$lib/workbench'
  import EChart from '$lib/charts/EChart.svelte'
  import AccountSwitcher from '$lib/components/account/AccountSwitcher.svelte'
  import AccountOverview from '$lib/components/account/AccountOverview.svelte'
  import PositionsTable from '$lib/components/account/PositionsTable.svelte'
  import OrdersTable from '$lib/components/account/OrdersTable.svelte'
  import DecisionDay from '$lib/components/account/DecisionDay.svelte'
  import CompareTable from '$lib/components/account/CompareTable.svelte'
  import NewAccountDialog from '$lib/components/account/NewAccountDialog.svelte'
  import OpeningDialog from '$lib/components/account/OpeningDialog.svelte'
  import { Alert, AlertDescription } from '$lib/ui/alert/index'
  import { Button } from '$lib/ui/button/index'
  import * as Card from '$lib/ui/card/index'
  import * as Dialog from '$lib/ui/dialog/index'
  import * as Select from '$lib/ui/select/index'
  import ConfirmDialog from '$lib/ui/confirm-dialog/ConfirmDialog.svelte'
  import { DataTable } from '$lib/ui/data-table/index'
  import type { Column } from '$lib/ui/data-table/types'
  import CalcVersion from '$lib/ui/domain/CalcVersion.svelte'
  import CutoffTime from '$lib/ui/domain/CutoffTime.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import PageHeader from '$lib/ui/domain/PageHeader.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import Field from '$lib/ui/field/Field.svelte'
  import { Input } from '$lib/ui/input/index'
  import TabNav from '$lib/ui/tab-nav/TabNav.svelte'
  import { notify } from '$lib/ui/toast'

  let { sub }: { sub?: string } = $props()
  const uid = $props.id()
  const SELECT = 'h-8 w-full min-w-0 rounded-md border border-input bg-card px-2 text-sm text-foreground'
  const NUM = 'text-right tabular-nums'
  const parts = $derived((sub ?? '').split('/'))
  const accountId = $derived(parts[0] && parts[0] !== 'compare' ? parts[0] : REAL_ACCOUNT_ID)
  const tab = $derived(parts[0] === 'compare' || parts[1] === 'compare' ? 'compare' : ['positions', 'trades', 'decisions'].includes(parts[1]) ? parts[1] : 'overview')
  let accounts = $state<ac.Account[]>([])
  let listError = $state('')
  let detail = $state<ac.AccountDetail | null>(null)
  let history = $state<ac.EquityHistory | null>(null)
  let otherHistories = $state<{ account: Pick<ac.Account, 'id' | 'name' | 'opened_on' | 'opening_session'>; history: ac.EquityHistory }[]>([])
  let trades = $state<ac.AccountTrade[]>([])
  let decisions = $state<ac.PaperDecision[]>([])
  let orders = $state<ac.PaperOrder[]>([])
  let comparison = $state<ac.CompareView | null>(null)
  let comparisonHistories = $state<{ id: string; name: string; history: ac.EquityHistory }[]>([])
  let comparisonHistoryErrors = $state<Record<string, string>>({})
  let legacyAccount = $state<wb.AccountView | null>(null)
  let error = $state('')
  let secondaryErrors = $state<string[]>([])
  let failedBlocks = $state<Record<string, string>>({})
  let loading = $state(true)
  let loadSequence = 0
  let listSequence = 0
  let newOpen = $state(false)
  let openingOpen = $state(false)
  const selected = $derived(detail?.account ?? accounts.find(account => account.id === accountId))
  const canRecordFunds = $derived(detail?.account.kind === 'real' && detail.account.status === 'active')
  const canWrite = $derived(canRecordFunds && detail?.forms.can_record_trades)
  let routeGeneration = 0
  let disposed = false
  onDestroy(() => { disposed = true; routeGeneration++ })
  const currentAction = (generation: number, targetId: string) => !disposed && generation === routeGeneration && targetId === accountId
  const displayOtherHistories = $derived(otherHistories.map(item => ({ ...item, account: accounts.find(account => account.id === item.account.id) ?? { ...item.account, opened_on: null } })))
  const cutoff = $derived(detail?.cutoff_at ?? detail?.equity?.computed_at)
  const month = $derived(comparison?.rows.find(row => row.key === 'month_return'))
  const compareIndex = $derived(comparison?.columns.findIndex(column => column.account_id === accountId) ?? -1)
  const tabs = $derived([
    ['overview', '概览'], ['positions', '持仓'], ['trades', '成交'],
    ...(selected?.kind === 'paper' ? [['decisions', selected.strategy === 'follow_plans' ? '执行记录' : 'Agent 决策']] : []), ['compare', '对比'],
  ].map(([id, label]) => ({ label, href: id === 'compare' ? '#account/compare' : `#account/${accountId}${id === 'overview' ? '' : `/${id}`}`, current: tab === id })))

  async function loadAccounts() {
    const sequence = ++listSequence
    try {
      const result = await ac.listAccounts(true)
      if (sequence !== listSequence) return
      accounts = result.accounts ?? []; listError = ''
    } catch (err) { if (sequence === listSequence) listError = ac.describeAccountError(err) }
  }
  $effect(() => { void loadAccounts() })

  async function loadPage(id: string, page: string, clear = true) {
    const sequence = ++loadSequence
    loading = clear; error = ''; secondaryErrors = []; failedBlocks = {}
    if (page === 'overview') history = null
    if (clear) {
      detail = null; history = null; legacyAccount = null
      trades = []; decisions = []; orders = []; comparison = null; comparisonHistories = []; comparisonHistoryErrors = {}; otherHistories = []
      expandedDay = ''; dayDetail = null; dayError = ''; daySequence++
    }
    try {
      if (page === 'compare') {
        const view = await ac.getCompare()
        if (sequence !== loadSequence) return
        comparison = view
        const results = await Promise.allSettled(view.columns.map(async column => ({ id: column.account_id, name: column.name, history: await ac.getEquity(column.account_id) })))
        if (sequence !== loadSequence) return
        comparisonHistories = results.flatMap(result => result.status === 'fulfilled' ? [result.value] : [])
        comparisonHistoryErrors = Object.fromEntries(results.flatMap((result, index) => result.status === 'rejected' ? [[view.columns[index].account_id, ac.describeAccountError(result.reason)]] : []))
        secondaryErrors = results.flatMap((result, index) => result.status === 'rejected' ? [`${view.columns[index].name} 曲线：${ac.describeAccountError(result.reason)}`] : [])
        return
      }
      const current = await ac.getAccount(id)
      if (sequence !== loadSequence) return
      detail = current
      const tasks: { key: string; run: Promise<unknown> }[] = []
      if (page === 'overview') {
        if (current.account.opened) tasks.push({ key: '权益曲线', run: ac.getEquity(id).then(value => { if (sequence === loadSequence) history = value }) })
        else history = { rows: [], spy: [] }
        tasks.push({ key: '本月统计', run: ac.getCompare().then(async value => {
          if (sequence !== loadSequence) return
          comparison = value
          const results = await Promise.allSettled(value.columns.filter(column => column.account_id !== id).map(async column => {
            const other = await ac.getEquity(column.account_id)
            return { account: { id: column.account_id, name: column.name, opened_on: null }, history: other }
          }))
          if (sequence !== loadSequence) return
          otherHistories = results.flatMap(result => result.status === 'fulfilled' ? [result.value] : [])
          secondaryErrors = [...secondaryErrors, ...results.flatMap(result => result.status === 'rejected' ? [`其他账户曲线：${ac.describeAccountError(result.reason)}`] : [])]
        }) })
        if (current.account.kind === 'real') tasks.push({ key: '资金快照与出入金', run: wb.getAccount(REAL_ACCOUNT_ID).then(value => { if (sequence === loadSequence) legacyAccount = value }) })
        if (current.account.kind === 'paper' && current.decision) tasks.push({ key: '今日决策', run: ac.getDecision(id, current.decision.session).then(value => {
          if (sequence === loadSequence && value.decision) detail = { ...current, decision: value.decision }
        }) })
      }
      if (page === 'trades') tasks.push({ key: '成交', run: ac.getAccountTrades(id).then(value => { if (sequence === loadSequence) trades = value.trades ?? [] }) })
      if (page === 'decisions' && current.account.kind === 'paper') {
        tasks.push({ key: '每日决策', run: ac.getDecisions(id).then(value => { if (sequence === loadSequence) decisions = value.decisions ?? [] }) })
        tasks.push({ key: '订单', run: ac.getOrders(id).then(value => { if (sequence === loadSequence) orders = value.orders ?? [] }) })
      }
      const results = await Promise.allSettled(tasks.map(task => task.run))
      if (sequence !== loadSequence) return
      failedBlocks = Object.fromEntries(results.flatMap((result, index) => result.status === 'rejected' ? [[tasks[index].key, ac.describeAccountError(result.reason)]] : []))
      secondaryErrors = [...secondaryErrors, ...results.flatMap((result, index) => result.status === 'rejected' ? [`${tasks[index].key}：${ac.describeAccountError(result.reason)}`] : [])]
    } catch (err) { if (sequence === loadSequence) error = ac.describeAccountError(err) }
    finally { if (sequence === loadSequence) loading = false }
  }
  $effect(() => { void loadPage(accountId, tab) })
  const refresh = async () => { await Promise.all([loadAccounts(), loadPage(accountId, tab, false)]) }

  let expandedDay = $state('')
  let dayDetail = $state<ac.PaperDecision | null>(null)
  let dayError = $state('')
  let dayLoading = $state(false)
  let daySequence = 0
  async function toggleDay(session: string) {
    if (expandedDay === session) { expandedDay = ''; daySequence++; return }
    expandedDay = session; dayDetail = null; dayError = ''; dayLoading = true
    const sequence = ++daySequence
    const id = accountId
    try {
      const result = await ac.getDecision(id, session)
      if (sequence === daySequence && id === accountId) dayDetail = result.decision
    } catch (err) { if (sequence === daySequence) dayError = ac.describeAccountError(err) }
    finally { if (sequence === daySequence) dayLoading = false }
  }

  let hiddenCurves = $state(new Set<string>())
  function toggleCurve(id: string) {
    const next = new Set(hiddenCurves)
    if (next.has(id)) next.delete(id); else next.add(id)
    hiddenCurves = next
  }
  const comparisonCurves = $derived(comparisonHistories.map(item => {
    const account = accounts.find(account => account.id === item.id)
    return { ...item, curve: ac.assessEquityHistory(item.history.rows, account?.opening_session ?? account?.opened_on ?? null) }
  }))
  const comparisonSpy = $derived(ac.mergeSpyHistory(comparisonHistories.map(item => item.history)))
  const comparisonCurveWarnings = $derived(comparisonCurves.flatMap(item => [
    ...item.curve.issues.map(issue => `${item.name} · ${issue.session} · ${issue.reason}`),
    ...(item.curve.baselineWarning ? [`${item.name} · ${item.curve.baselineWarning}`] : []),
  ]))
  const comparisonValuationWarnings = $derived.by(() => {
    const warnings: Record<string, string[]> = {}
    for (const item of comparisonCurves) warnings[item.id] = item.curve.issues.map(issue => `${issue.session}：${issue.reason}`)
    for (const account of accounts) {
      const warning = ac.valuationWarning(account.latest?.detail)
      if (warning) warnings[account.id] = [...(warnings[account.id] ?? []), `${account.latest!.session}：${warning}`]
    }
    for (const [id, message] of Object.entries(comparisonHistoryErrors)) warnings[id] = [...(warnings[id] ?? []), `权益历史读取失败，未核验估值完整性：${message}`]
    return Object.fromEntries(Object.entries(warnings).map(([id, notes]) => [id, [...new Set(notes)]]))
  })
  const compareOption = $derived<EChartsOption>({
    tooltip: { trigger: 'axis' }, legend: { type: 'scroll' }, grid: { left: 50, right: 20, top: 50, bottom: 35 },
    xAxis: { type: 'time' }, yAxis: { type: 'value', scale: true, name: '首个交易日 = 100' },
    series: [
      ...comparisonCurves.filter(item => !hiddenCurves.has(item.id)).map(item => ({ name: item.name, type: 'line' as const, showSymbol: false, connectNulls: false, data: item.curve.data })),
      ...(!hiddenCurves.has('SPY') && comparisonSpy.length ? [{ name: 'SPY', type: 'line' as const, showSymbol: false, data: ac.normalizedEquity(comparisonSpy.map(row => ({ session: row.session, equity: row.close }))) }] : []),
    ],
  })

  const paramFields = [
    ['risk_pct', '单笔风险比例'], ['single_cap', '单票上限比例'], ['total_cap', '总持仓上限比例'], ['fee_per_trade', '每笔费用'],
    ['max_positions', '最多持仓数'], ['reconcile_tolerance_pct', '对账容差比例'], ['reconcile_tolerance_abs', '对账容差金额'],
  ]
  let paramsOpen = $state(false)
  let paramValues = $state<Record<string, string>>({})
  let paramReason = $state('')
  let paramError = $state('')
  let paramBusy = $state(false)
  let paramRequestId = ''
  let paramTargetId = ''
  let paramsGeneration = 0
  function editParams() {
    paramsGeneration++; paramBusy = false
    paramValues = Object.fromEntries(Object.entries(detail?.account.params ?? {}).map(([key, value]) => [key, String(value)]))
    paramReason = ''; paramError = ''; paramRequestId = wb.newRequestId(); paramTargetId = detail?.account.id ?? ''; paramsOpen = true
  }
  async function saveParams(event: Event) {
    event.preventDefault()
    if (!detail || paramBusy || !paramReason.trim() || paramTargetId !== accountId) return
    const generation = routeGeneration, dialog = paramsGeneration, targetId = paramTargetId
    const current = () => currentAction(generation, targetId) && dialog === paramsGeneration
    paramBusy = true; paramError = ''
    try {
      const params = Object.fromEntries(Object.entries(paramValues).filter(([, value]) => value.trim()).map(([key, value]) => [key, key === 'max_positions' ? Number(value) : value]))
      await ac.changeParams(targetId, { request_id: paramRequestId, params, reason: paramReason.trim() })
      if (!current()) return
      paramsOpen = false; notify.success('参数已记录，下一交易日生效'); await refresh()
    } catch (err) { if (current()) paramError = ac.describeAccountError(err) }
    finally { if (current()) paramBusy = false }
  }
  let closeOpen = $state(false)
  let closeReason = $state('')
  let closeBusy = $state(false)
  let actionError = $state('')
  let closeTargetId = ''
  let closeGeneration = 0
  function askClose() {
    if (!detail || detail.account.kind !== 'paper') return
    closeGeneration++; closeBusy = false; closeReason = ''; actionError = ''; closeTargetId = detail.account.id; closeOpen = true
  }
  async function closePaper() {
    if (!detail || detail.account.kind !== 'paper' || closeBusy || !closeReason.trim() || closeTargetId !== accountId) return
    const generation = routeGeneration, dialog = closeGeneration, targetId = closeTargetId
    const current = () => currentAction(generation, targetId) && dialog === closeGeneration
    closeBusy = true; actionError = ''
    try { await ac.closeAccount(targetId, closeReason.trim()); if (!current()) return; closeOpen = false; await refresh() }
    catch (err) { if (current()) actionError = ac.describeAccountError(err) }
    finally { if (current()) closeBusy = false }
  }

  // 关联计划（spec §6.1）：'' = 没动过（按默认项），NONE = 无计划，OWN = 自定，其余是计划行 id
  const NONE = '__none__'
  const OWN = '__own__'
  const emptyForm = () => ({ ticker: '', side: 'buy', quantity: '', price: '', fee: '0', executed: '', planChoice: '', note: '',
                             own: { entry_low: '', entry_high: '', stop: '', target1: '', max_hold: '' },
                             requestId: wb.newRequestId() })
  let form = $state(emptyForm())
  let linkable = $state<wb.LinkablePlansView | null>(null)
  let linkableError = $state('')
  let linkableSeq = 0
  let formError = $state('')
  let recording = $state(false)
  let voidReason = $state('')
  // 作废：点「作废」打开 ConfirmDialog，理由在对话框里填，填了才能点「确认作废」
  let voidAsk = $state<{ kind: 'trade' | 'flow'; id: string; what: string } | null>(null)
  let voidOpen = $state(false)
  let voidBusy = $state(false)


  const tickerKey = $derived(form.ticker.trim().toUpperCase())

  // 可关联的计划按成交时刻取（该标的活跃链的最新版、有效期覆盖成交日的规则基线、最近的自定计划）；
  // 代码或成交时间变了就重新取，晚到的旧响应丢弃。
  async function loadLinkable(ticker: string, executed: string) {
    const seq = ++linkableSeq
    const at = new Date(executed)
    try {
      const res = await wb.getLinkablePlans(ticker, (Number.isNaN(at.getTime()) ? new Date() : at).toISOString())
      if (seq !== linkableSeq) return
      linkable = { plans: Array.isArray(res?.plans) ? res.plans : [], default_plan_id: res?.default_plan_id ?? null }
      linkableError = ''
    } catch (e) {
      if (seq !== linkableSeq) return
      linkable = null
      linkableError = wb.describeError(e)
    }
  }
  // 可关联的计划是按代码与成交时间取的：代码变了，之前选的计划（和自定价位）不再属于这只票；成交时间变了，
  // 选中的计划行也可能不再可关联——一律清掉重选（「自定」本身与成交时间无关，保留）。
  let lastKey = ''
  let lastExecuted = ''
  $effect(() => {
    const ticker = tickerKey
    const executed = form.executed
    untrack(() => {
      if (ticker !== lastKey) {
        form.planChoice = ''
        form.own = { entry_low: '', entry_high: '', stop: '', target1: '', max_hold: '' }
      } else if (executed !== lastExecuted && form.planChoice !== OWN) {
        form.planChoice = ''
      }
      lastKey = ticker
      lastExecuted = executed
    })
    if (!ticker) { linkableSeq++; linkable = null; linkableError = ''; return }
    const timer = setTimeout(() => { void loadLinkable(ticker, executed) }, 250)   // 输入代码时不逐字请求
    return () => clearTimeout(timer)
  })

  // 买入默认选活跃链最新版，没有时「无计划」；卖出可不关联（平仓按 FIFO 配对）
  // 选中的计划 id 不在当前可关联列表里（列表还没取到、取失败、换了票）就当没选过
  const choiceValid = $derived(form.planChoice === NONE || form.planChoice === OWN
    || !!linkable?.plans.some(p => p.plan_id === form.planChoice))
  const planChoice = $derived((form.planChoice && choiceValid ? form.planChoice : '')
    || (form.side === 'buy' && linkable?.default_plan_id && linkable.plans.some(p => p.plan_id === linkable!.default_plan_id)
        ? linkable.default_plan_id : NONE))
  const ownError = $derived.by(() => {
    if (planChoice !== OWN) return null
    const o = form.own
    if (o.entry_low.trim() === '' && o.entry_high.trim() === '' && o.stop.trim() === '' && o.target1.trim() === '') return null
    const levels = wb.levelsError(o)
    if (levels) return levels
    const hold = o.max_hold.trim()
    if (hold !== '' && !/^[1-9][0-9]*$/.test(hold)) return '最长持有须为正整数（交易日）'
    return null
  })
  const ownBlank = $derived(planChoice === OWN && form.own.entry_low.trim() === '')

  async function recordTrade(e: Event) {
    e.preventDefault()
    if (recording || !canWrite) return
    formError = ''
    const executed = new Date(form.executed)
    if (Number.isNaN(executed.getTime())) { formError = '请填写成交时间'; return }
    if (planChoice === OWN && (ownBlank || ownError)) { formError = ownError ?? '自定计划需要填写四个价位'; return }
    const generation = routeGeneration, targetId = accountId
    const link: Partial<wb.TradeBody> = planChoice === OWN
      ? { own_plan: { entry_low: form.own.entry_low.trim(), entry_high: form.own.entry_high.trim(), stop: form.own.stop.trim(),
                      target1: form.own.target1.trim(),
                      ...(form.own.max_hold.trim() ? { max_hold_sessions: Number(form.own.max_hold.trim()) } : {}) } }
      : planChoice !== NONE ? { plan_id: planChoice } : {}
    recording = true
    try {
      await wb.postTrade({
        account_id: REAL_ACCOUNT_ID, request_id: form.requestId, ticker: tickerKey, side: form.side as 'buy' | 'sell',
        quantity: String(form.quantity), price: String(form.price), fee: String(form.fee || '0'),
        executed_at: executed.toISOString(), note: form.note || null, ...link,
      })
      if (!currentAction(generation, targetId)) return
      form = emptyForm()
      notify.success('已记录成交')
      await refresh()
    } catch (err) {
      if (!currentAction(generation, targetId)) return
      formError = ac.describeAccountError(err)
      // 409 plan_stale / plan_after_fill：可关联的版本变了，重新取一次让用户改选
      if (err instanceof wb.WorkbenchError && err.status === 409 && tickerKey) void loadLinkable(tickerKey, form.executed)
    } finally {
      if (currentAction(generation, targetId)) recording = false
    }
  }


  const snapshotForm = () => ({ as_of: '', equity: '', note: '', requestId: wb.newRequestId() })
  let snap = $state(snapshotForm())
  const flowForm = () => ({ flow_date: '', amount: '', note: '', requestId: wb.newRequestId() })
  let flow = $state(flowForm())
  let accountError = $state('')

  let snapBusy = $state(false)
  let flowBusy = $state(false)
  async function recordSnapshot(e: Event) {
    e.preventDefault()
    if (snapBusy || !canRecordFunds) return
    const generation = routeGeneration, targetId = accountId
    accountError = ''
    snapBusy = true
    try {
      await wb.postSnapshot({ account_id: REAL_ACCOUNT_ID, request_id: snap.requestId, as_of: snap.as_of, equity: String(snap.equity),
                              note: snap.note || null })
      if (!currentAction(generation, targetId)) return
      snap = snapshotForm()
      notify.success('已录入资金快照')
      await refresh()
    } catch (err) {
      if (currentAction(generation, targetId)) accountError = ac.describeAccountError(err)
    } finally {
      if (currentAction(generation, targetId)) snapBusy = false
    }
  }

  async function recordFlow(e: Event) {
    e.preventDefault()
    if (flowBusy || !canRecordFunds) return
    const generation = routeGeneration, targetId = accountId
    accountError = ''
    flowBusy = true
    try {
      await wb.postFlow({ account_id: REAL_ACCOUNT_ID, request_id: flow.requestId, flow_date: flow.flow_date, amount: String(flow.amount),
                          note: flow.note || null })
      if (!currentAction(generation, targetId)) return
      flow = flowForm()
      notify.success('已录入出入金')
      await refresh()
    } catch (err) {
      if (currentAction(generation, targetId)) accountError = ac.describeAccountError(err)
    } finally {
      if (currentAction(generation, targetId)) flowBusy = false
    }
  }



  function askVoid(kind: 'trade' | 'flow', id: string, what: string) {
    voidGeneration++; voidBusy = false; actionError = ''
    voidAsk = { kind, id, what }; voidReason = ''; voidOpen = true
  }
  async function runVoid() {
    const ask = voidAsk
    if (!ask || (ask.kind === 'trade' ? !canWrite : !canRecordFunds) || voidBusy) return
    const generation = routeGeneration, targetId = accountId, dialog = voidGeneration
    const current = () => currentAction(generation, targetId) && dialog === voidGeneration
    voidBusy = true
    try {
      if (ask.kind === 'trade') await wb.voidTrade(ask.id, voidReason.trim())
      else await wb.voidFlow(ask.id, voidReason.trim())
      if (!current()) return
      notify.success('记录已作废'); voidOpen = false; await refresh()
    } catch (err) { if (current()) actionError = ac.describeAccountError(err) }
    finally { if (current()) voidBusy = false }
  }
  let voidGeneration = 0
  // Dialogs belong to the account that opened them; navigation never retargets a pending form.
  $effect(() => {
    void accountId
    untrack(() => {
      routeGeneration++; paramsGeneration++; closeGeneration++; voidGeneration++; linkableSeq++
      paramsOpen = false; closeOpen = false; openingOpen = false; voidOpen = false; newOpen = false
      paramBusy = false; closeBusy = false; voidBusy = false; recording = false; snapBusy = false; flowBusy = false
      actionError = ''; paramError = ''; formError = ''; accountError = ''; linkableError = ''; linkable = null
      form = emptyForm(); snap = snapshotForm(); flow = flowForm()
    })
  })
  const tradeColumns = $derived<Column<ac.AccountTrade>[]>([
    { key: 'executed_at', header: '成交时间（北京）', value: trade => fmtBeijing(trade.executed_at) },
    { key: 'ticker', header: '标的' }, { key: 'side', header: '方向', value: trade => trade.side === 'buy' ? '买入' : '卖出' },
    { key: 'quantity', header: '股数', align: 'num' }, { key: 'price', header: '成交价', align: 'num' }, { key: 'fee', header: '费用', align: 'num' },
    { key: 'plan', header: '关联计划', cell: tradePlan }, { key: 'recorded_by', header: '记录来源', value: trade => trade.recorded_by ?? '—' },
    { key: 'fill', header: '成交方式 / 退出原因', value: trade => [
      trade.detail?.fill_case ? (ac.FILL_CASE_TEXT as Record<string, string>)[trade.detail.fill_case] ?? trade.detail.fill_case : '',
      trade.exit_reason || trade.detail?.exit_reason ? (ac.EXIT_REASON_TEXT as Record<string, string>)[trade.exit_reason ?? trade.detail!.exit_reason!] ?? trade.exit_reason ?? trade.detail!.exit_reason : '',
    ].filter(Boolean).join(' · ') || '—' },
    { key: 'note', header: '备注', value: trade => trade.note ?? '—' }, { key: 'void', header: '记录状态', cell: tradeVoid },
  ])
  const closedColumns: Column<ac.ClosedPosition>[] = [
    { key: 'ticker', header: '标的' }, { key: 'opened', header: '买入日' }, { key: 'closed', header: '平仓日' },
    { key: 'holding_sessions', header: '持有交易日', align: 'num' }, { key: 'realized_pnl', header: '已实现盈亏', align: 'num', value: position => ac.decimalText(position.realized_pnl) },
    { key: 'return', header: '收益率', align: 'num', value: position => position.return == null ? '—' : wb.pct(position.return) },
    { key: 'spy_return', header: '同期 SPY', align: 'num', value: position => position.spy_return == null ? '—' : wb.pct(position.spy_return) },
    { key: 'max_favorable', header: '最大浮盈', align: 'num', value: position => position.max_favorable == null ? '—' : wb.pct(position.max_favorable) },
    { key: 'max_adverse', header: '最大浮亏', align: 'num', value: position => position.max_adverse == null ? '—' : wb.pct(position.max_adverse) },
  ]
</script>

{#snippet tradePlan(trade: ac.AccountTrade)}
  {#if trade.plan_id}<a class="text-primary underline" href={`#plan/${trade.plan_id}`}>{trade.plan_label ?? trade.plan_id}</a>
  {:else if trade.decision_id}旧决定{:else if trade.side === 'buy'}无计划（计划外）{:else}—{/if}
{/snippet}
{#snippet tradeVoid(trade: ac.AccountTrade)}
  {#if trade.voided_at}<span class="text-muted-foreground line-through">已作废：{trade.void_reason}</span>
  {:else if canWrite}<Button variant="ghost" size="sm" aria-label={`作废成交 ${trade.ticker}`} onclick={() => askVoid('trade', trade.id, `${trade.ticker} ${trade.quantity} @ ${trade.price}`)}>作废</Button>
  {:else}有效{/if}
{/snippet}

<div class="grid min-w-0 gap-4">
  <PageHeader level={1} title={tab === 'compare' ? '账户对比' : detail?.account.name ?? '账户'}>
    {#snippet meta()}<CalcVersion version="account-1" /><CutoffTime at={cutoff ?? comparison?.cutoff_at} />{#if selected?.kind === 'paper' || tab === 'compare'}<CalcVersion version="paper-fill-1" />{/if}{/snippet}
    {#snippet actions()}
      {#if detail && detail.account.status === 'active' && tab !== 'compare'}
        <Button variant="outline" onclick={editParams}>修改参数</Button>
        {#if detail.account.kind === 'paper'}<Button variant="outline" onclick={askClose}>关闭模拟账户</Button>{/if}
      {/if}
    {/snippet}
  </PageHeader>
  {#if listError}<DataBlock state="error" message={listError} onRetry={loadAccounts} />{/if}
  <AccountSwitcher {accounts} selectedId={tab === 'compare' ? '' : accountId} onnew={() => newOpen = true} />
  <TabNav label="账户页签" items={tabs} />
  {#if selected?.kind === 'paper' || tab === 'compare'}<p class="text-sm font-medium text-muted-foreground">模拟成交，无滑点</p>{/if}
  {#if actionError}<DataBlock state="error" message={actionError} />{/if}
  {#each secondaryErrors as message}<DataBlock state="degraded" {message} />{/each}
  <DataBlock state={error ? 'error' : loading ? 'loading' : 'ready'} message={error} onRetry={refresh} skeleton="table">
    {#if tab === 'compare' && comparison}
      <Section level={2} title="权益曲线叠加">
        <div class="flex flex-wrap items-center gap-3 text-xs text-muted-foreground"><CalcVersion version="account-1" /><CalcVersion version="paper-fill-1" /><CutoffTime at={comparison.cutoff_at} /></div>
        <div class="flex flex-wrap gap-2">{#each comparison.columns as column}<Button size="sm" variant="outline" aria-pressed={!hiddenCurves.has(column.account_id)} onclick={() => toggleCurve(column.account_id)}>{column.name}</Button>{/each}<Button size="sm" variant="outline" aria-pressed={!hiddenCurves.has('SPY')} onclick={() => toggleCurve('SPY')}>SPY</Button></div>
        <p class="text-xs text-muted-foreground">各账户曲线归一到开账当日或之后的首个交易日 = 100；首个交易日不同，收益区间不同。</p>
        {#each comparisonCurveWarnings as message}<DataBlock state="degraded" {message} />{/each}
        <p class="text-xs text-muted-foreground">估值不完整的日期留空，不连接缺口；首个交易日不可用时不绘制该账户曲线。</p>
        <DataBlock state={comparisonHistories.some(item => item.history.rows.length) ? 'ready' : 'empty'} message="尚无可比较的每日权益">
          {#key [...hiddenCurves].sort().join(',')}<div class="min-w-0" role="img" aria-label="账户对比权益曲线"><EChart option={compareOption} /></div>{/key}
        </DataBlock>
      </Section>
      <CompareTable view={comparison} valuationWarnings={comparisonValuationWarnings} />
    {:else if detail}
      {#if detail.account.status === 'closed'}<DataBlock state="degraded" message="账户已关闭，只读查看历史记录。" />{/if}
      {#if tab === 'overview'}
        {#if !detail.account.opened && detail.account.kind === 'real'}
          <DataBlock state="not_computed" message="真实账户尚未开账，请先核对期初现金与已有持仓。" />
          <div><Button variant="primary" onclick={() => openingOpen = true}>开账</Button></div>
        {/if}
        <AccountOverview {detail} {history} otherHistories={displayOtherHistories} historyError={failedBlocks['权益曲线']} monthReturn={month?.values[compareIndex]?.text} monthReturnNote={month?.values[compareIndex]?.note} spyReturn={month?.spy} />
        {#if detail.account.kind === 'paper'}
          <Section level={2} title={detail.account.strategy === 'follow_plans' ? '今日执行摘要' : '今日决策摘要'}>
            <div class="flex flex-wrap items-center gap-3 text-xs text-muted-foreground"><CalcVersion version="paper-fill-1" /><CutoffTime at={cutoff} /></div>
            {#if detail.decision}
              <p class="text-xs text-muted-foreground">决策日 {detail.decision.session}</p>
              <p class="whitespace-pre-wrap break-all text-sm">{detail.decision.summary == null ? '未提供总结' : typeof detail.decision.summary === 'string' ? detail.decision.summary : JSON.stringify(detail.decision.summary)}</p>
              {#if detail.decision.status === 'skipped'}<DataBlock state="degraded" message={`跳过原因：${ac.decisionReasonText(detail.decision.status_reason)}`} />{/if}
            {/if}
            <OrdersTable orders={detail.decision?.orders ?? detail.pending_orders} />
          </Section>
        {:else}
          <Section level={2} title="券商快照对账">
            <div class="flex flex-wrap items-center gap-3 text-xs text-muted-foreground"><CalcVersion version="account-1" /><CutoffTime at={cutoff} /></div>
            <DataBlock state={detail.reconciliation === null ? 'not_computed' : detail.reconciliation.length ? 'ready' : 'empty'} message={detail.reconciliation === null ? '尚未计算对账，请先录入券商资金快照' : '暂无券商快照可对账'}>
              <ul class="grid gap-2 text-sm">{#each detail.reconciliation ?? [] as row}<li class="flex flex-wrap items-center gap-2"><span>{row.session ?? row.as_of ?? ''}</span><StatusPill domain="reconcile" value={row.status} /><span>账本 {ac.decimalText(row.ledger_equity)} · 券商 {ac.decimalText(row.snapshot_equity)} · 差额 {ac.decimalText(row.difference ?? row.diff)}</span>{#if row.note}<span>{row.note}</span>{/if}</li>{/each}</ul>
            </DataBlock>
          </Section>
        {/if}
        <Section level={2} title="当前持仓">
          <div class="flex flex-wrap items-center gap-3 text-xs text-muted-foreground"><CalcVersion version="account-1" /><CutoffTime at={cutoff} /></div>
          <PositionsTable positions={detail.positions} valuationSession={detail.equity?.session ?? detail.account.settled_through} valuationDetail={detail.equity?.detail} />
        </Section>
        {#if canRecordFunds}
          <Section level={2} title="真实账户记录">
            <Field label="录入账户" id={`${uid}-locked-account`}>{#snippet children({ id, describedby })}<Select.Root type="single" value={REAL_ACCOUNT_ID} disabled><Select.Trigger {id} aria-describedby={describedby}>{detail?.account.name ?? '真实账户'}</Select.Trigger></Select.Root>{/snippet}</Field>
            {#if canWrite}<Section level={3} title="录入成交">
                  <Card.Root size="sm" class="px-3">
      <form class="grid grid-cols-1 gap-3 text-sm md:grid-cols-2 lg:grid-cols-4" onsubmit={recordTrade}>
        <Field label="代码" id={`${uid}-ticker`}>
          {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} bind:value={form.ticker} />{/snippet}
        </Field>
        <Field label="方向" id={`${uid}-side`}>
          {#snippet children({ id, describedby })}
            <select {id} aria-describedby={describedby} class={SELECT} bind:value={form.side}>
              <option value="buy">买入</option><option value="sell">卖出</option>
            </select>
          {/snippet}
        </Field>
        <Field label="数量" id={`${uid}-quantity`}>
          {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} inputmode="decimal" class={NUM} bind:value={form.quantity} />{/snippet}
        </Field>
        <Field label="成交价" id={`${uid}-price`}>
          {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} inputmode="decimal" class={NUM} bind:value={form.price} />{/snippet}
        </Field>
        <Field label="费用" id={`${uid}-fee`}>
          {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} inputmode="decimal" class={NUM} bind:value={form.fee} />{/snippet}
        </Field>
        <Field label="成交时间（本机时区）" id={`${uid}-executed`}>
          {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} aria-label="成交时间" type="datetime-local" bind:value={form.executed} />{/snippet}
        </Field>
        <Field label="关联计划" id={`${uid}-plan`}>
          {#snippet children({ id, describedby })}
            <select {id} aria-describedby={describedby} class={SELECT} value={planChoice}
                    onchange={e => form.planChoice = (e.currentTarget as HTMLSelectElement).value}>
              <option value={NONE}>无计划</option>
              {#each linkable?.plans ?? [] as p (p.plan_id)}<option value={p.plan_id}>{p.label}</option>{/each}
              <option value={OWN}>自定</option>
            </select>
          {/snippet}
        </Field>
        <Field label="备注" id={`${uid}-note`}>
          {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} bind:value={form.note} />{/snippet}
        </Field>
        {#if linkableError}<Alert variant="warn" role="status" class="col-span-full"><AlertDescription>可关联的计划暂不可用：{linkableError}</AlertDescription></Alert>{/if}
        {#if planChoice === OWN}
          <div class="col-span-full grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-5" role="group" aria-label="自定计划">
            <Field label="入场下沿" id={`${uid}-own-low`}>
              {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} inputmode="decimal" class={NUM} bind:value={form.own.entry_low} />{/snippet}
            </Field>
            <Field label="入场上沿" id={`${uid}-own-high`}>
              {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} inputmode="decimal" class={NUM} bind:value={form.own.entry_high} />{/snippet}
            </Field>
            <Field label="止损" id={`${uid}-own-stop`}>
              {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} inputmode="decimal" class={NUM} bind:value={form.own.stop} />{/snippet}
            </Field>
            <Field label="T1" id={`${uid}-own-t1`}>
              {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} inputmode="decimal" class={NUM} bind:value={form.own.target1} />{/snippet}
            </Field>
            <Field label="最长持有（交易日，缺省 10）" id={`${uid}-own-hold`}>
              {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} aria-label="最长持有" inputmode="numeric" class={NUM} bind:value={form.own.max_hold} />{/snippet}
            </Field>
            {#if ownError}<Alert variant="danger" class="col-span-full"><AlertDescription>{ownError}</AlertDescription></Alert>{/if}
          </div>
        {/if}
        <div class="col-span-full flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" busy={recording} disabled={!!ownError}>记录成交</Button>
          {#if formError}<Alert variant="danger" class="w-auto py-1"><AlertDescription class="text-xs">{formError}</AlertDescription></Alert>{/if}
        </div>
      </form>
    </Card.Root>


            </Section>{/if}
            {#if accountError}<DataBlock state="error" message={accountError} />{/if}
                  <div class="grid gap-3 md:grid-cols-2">
        <Card.Root size="sm" class="px-3">
          <form class="grid gap-3 text-sm" onsubmit={recordSnapshot}>
            <h2 class="text-sm font-medium">录入资金快照</h2>
            <Field label="资金日期" id={`${uid}-snap-date`}>
              {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} type="date" bind:value={snap.as_of} />{/snippet}
            </Field>
            <Field label="总资金" id={`${uid}-snap-equity`}>
              {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} inputmode="decimal" class={NUM} bind:value={snap.equity} />{/snippet}
            </Field>
            <Field label="备注" id={`${uid}-snap-note`}>
              {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} aria-label="快照备注" bind:value={snap.note} />{/snippet}
            </Field>
            <div><Button type="submit" variant="primary" busy={snapBusy} disabled={!snap.as_of || !snap.equity}>录入资金快照</Button></div>
          </form>
        </Card.Root>
        <Card.Root size="sm" class="px-3">
          <form class="grid gap-3 text-sm" onsubmit={recordFlow}>
            <h2 class="text-sm font-medium">录入出入金</h2>
            <Field label="出入金日期" id={`${uid}-flow-date`}>
              {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} type="date" bind:value={flow.flow_date} />{/snippet}
            </Field>
            <Field label="金额（入金为正）" id={`${uid}-flow-amount`}>
              {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} aria-label="金额" inputmode="decimal" class={NUM} bind:value={flow.amount} />{/snippet}
            </Field>
            <Field label="备注" id={`${uid}-flow-note`}>
              {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} aria-label="出入金备注" bind:value={flow.note} />{/snippet}
            </Field>
            <div><Button type="submit" variant="primary" busy={flowBusy} disabled={!flow.flow_date || !flow.amount}>录入出入金</Button></div>
          </form>
        </Card.Root>
      </div>

          </Section>
        {/if}
        {#if detail.account.kind === 'real' && legacyAccount}
                <Section level={2} title="资金快照">
        <ul class="grid gap-1 text-sm">
          {#each legacyAccount.snapshots ?? [] as s (s.id)}
            <li>{s.as_of} · {s.equity}{s.note ? `（${s.note}）` : ''}{s.supersedes_id ? ' · 更正版' : ''} · 录入于 {wb.dateTime(s.recorded_at)}</li>
          {:else}<li class="text-muted-foreground">暂无快照</li>{/each}
        </ul>
      </Section>
      <Section level={2} title="出入金">
        <ul class="grid gap-1 text-sm">
          {#each legacyAccount.flows ?? [] as f (f.id)}
            <li class={f.voided_at ? 'text-muted-foreground line-through' : ''}>
              {f.flow_date} · {Number(f.amount) > 0 ? '入金' : '出金'} {f.amount}{f.note ? `（${f.note}）` : ''}
              {#if f.voided_at}<span>（已作废：{f.void_reason}）</span>
              {:else if canRecordFunds}
                <Button variant="ghost" size="sm" class="h-6 px-2 text-xs text-muted-foreground"
                        onclick={() => askVoid('flow', f.id, `${f.flow_date} ${Number(f.amount) > 0 ? '入金' : '出金'} ${f.amount}`)}>作废</Button>
              {/if}
            </li>
          {:else}<li class="text-muted-foreground">暂无出入金</li>{/each}
        </ul>
      </Section>

        {/if}
      {:else if tab === 'positions'}
        <Section level={2} title="全部持仓">
          <div class="flex flex-wrap items-center gap-3 text-xs text-muted-foreground"><CalcVersion version="account-1" /><CutoffTime at={cutoff} /></div>
          <PositionsTable positions={detail.positions} full valuationSession={detail.equity?.session ?? detail.account.settled_through} valuationDetail={detail.equity?.detail} />
          <p class="text-xs text-muted-foreground">模拟账户的同一标的仅保留一笔开放持仓；已持有时不能重复开仓。</p>
        </Section>
        <Section level={2} title="已平仓批次">
          <div class="flex flex-wrap items-center gap-3 text-xs text-muted-foreground"><CalcVersion version="account-1" /><CutoffTime at={cutoff} /></div>
          <DataBlock state={detail.closed_positions?.length ? 'ready' : 'empty'} message="暂无已平仓批次">
            <DataTable columns={closedColumns} rows={detail.closed_positions ?? []} rowKey={position => `${position.ticker}-${position.opened}-${position.closed}`} ariaLabel="已平仓批次" cards="narrow" />
          </DataBlock>
        </Section>
      {:else if tab === 'trades'}
        <Section level={2} title="成交记录">
          <div class="flex flex-wrap items-center gap-3 text-xs text-muted-foreground"><CalcVersion version={detail.account.kind === 'paper' ? 'paper-fill-1' : 'account-1'} /><CutoffTime at={cutoff} /></div>
          <DataBlock state={failedBlocks['成交'] ? 'error' : trades.length ? 'ready' : 'empty'} message={failedBlocks['成交'] || '暂无成交'}>
            <DataTable columns={tradeColumns} groups={[
              { key: 'valid', label: '有效成交', rows: trades.filter(trade => !trade.voided_at) },
              { key: 'voided', label: '已作废成交', rows: trades.filter(trade => !!trade.voided_at), muted: true },
            ]} rowKey={trade => trade.id} ariaLabel="账户成交" cards="narrow" />
          </DataBlock>
        </Section>
      {:else if tab === 'decisions' && detail.account.kind === 'paper'}
        <Section level={2} title={detail.account.strategy === 'follow_plans' ? '执行记录' : 'Agent 决策'}>
          <div class="flex flex-wrap items-center gap-3 text-xs text-muted-foreground"><CalcVersion version="paper-fill-1" /><CutoffTime at={cutoff} /></div>
          <DataBlock state={failedBlocks['每日决策'] ? 'error' : decisions.length ? 'ready' : 'empty'} message={failedBlocks['每日决策'] || '暂无每日决策记录'}>
            <div class="grid gap-2">
              {#each decisions as day (day.id)}
                <div class="grid gap-3 rounded-md border border-border bg-card p-3">
                  <div class="flex flex-wrap items-center gap-3">
                    <Button variant="link" size="sm" aria-label={`${expandedDay === day.session ? '收起' : '展开'} ${day.session}`}
                            aria-expanded={expandedDay === day.session} onclick={() => toggleDay(day.session)}>{day.session}</Button>
                    <StatusPill domain="paper_decision" value={day.status} />
                    <span class="text-xs text-muted-foreground">下单 {day.orders_placed ?? '—'} · 拒绝 {day.orders_rejected ?? '—'}{day.status_reason ? ` · ${ac.decisionReasonText(day.status_reason)}` : ''}</span>
                  </div>
                  {#if expandedDay === day.session}
                    <DataBlock state={dayLoading ? 'loading' : dayError ? 'error' : dayDetail ? 'ready' : 'empty'} message={dayError || '该日未保存决策详情'}>
                      {#if dayDetail}<DecisionDay decision={dayDetail} />{/if}
                    </DataBlock>
                  {/if}
                </div>
              {/each}
            </div>
          </DataBlock>
        </Section>
        <Section level={2} title="全部订单">
          <div class="flex flex-wrap items-center gap-3 text-xs text-muted-foreground"><CalcVersion version="paper-fill-1" /><CutoffTime at={cutoff} /></div>
          <DataBlock state={failedBlocks['订单'] ? 'error' : 'ready'} message={failedBlocks['订单']}><OrdersTable {orders} /></DataBlock>
        </Section>
      {:else}<DataBlock state="empty" message="真实账户没有 Agent 决策记录" />{/if}
    {/if}
  </DataBlock>
</div>

<NewAccountDialog bind:open={newOpen} oncreated={account => { void loadAccounts(); location.hash = `#account/${account.id}` }} />
{#if detail?.account.kind === 'real'}<OpeningDialog bind:open={openingOpen} account={detail.account} onopened={() => { void refresh() }} />{/if}
<Dialog.Root bind:open={paramsOpen}>
  <Dialog.Content class="max-h-[90vh] overflow-y-auto" showCloseButton={!paramBusy}
                  interactOutsideBehavior={paramBusy ? 'ignore' : 'close'} escapeKeydownBehavior={paramBusy ? 'ignore' : 'close'}>
    <Dialog.Header><Dialog.Title>修改账户参数</Dialog.Title><Dialog.Description>新参数于下一交易日生效；历史账本保留当日参数。</Dialog.Description></Dialog.Header>
    <form class="grid gap-3" onsubmit={saveParams}>
      <div class="grid gap-3 sm:grid-cols-2">
        {#each paramFields as [key, label]}
          <Field {label} id={`${uid}-param-${key}`}>
            {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} inputmode={key === 'max_positions' ? 'numeric' : 'decimal'} class={NUM} bind:value={paramValues[key]} disabled={paramBusy} />{/snippet}
          </Field>
        {/each}
      </div>
      <Field label="修改理由" id={`${uid}-params-reason`}>
        {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} bind:value={paramReason} />{/snippet}
      </Field>
      {#if paramError}<DataBlock state="error" message={paramError} />{/if}
      <Dialog.Footer><Button variant="outline" disabled={paramBusy} onclick={() => paramsOpen = false}>取消</Button><Button type="submit" variant="primary" busy={paramBusy} disabled={!paramReason.trim()}>保存参数</Button></Dialog.Footer>
    </form>
  </Dialog.Content>
</Dialog.Root>
<ConfirmDialog bind:open={closeOpen} destructive busy={closeBusy} title="关闭模拟账户？"
               consequence="关闭后停止自动决策与撮合，历史记录保留，只能读取。" actionLabel="确认关闭账户"
               confirmDisabled={!closeReason.trim()} onConfirm={closePaper}>
  <Field label="关闭理由" id={`${uid}-close-reason`}>
    {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} bind:value={closeReason} />{/snippet}
  </Field>
  {#if actionError}<DataBlock state="error" message={actionError} />{/if}
</ConfirmDialog>
<ConfirmDialog bind:open={voidOpen} destructive busy={voidBusy} title={voidAsk?.kind === 'flow' ? '作废这笔出入金？' : '作废这笔成交？'}
               consequence={`${voidAsk?.what ?? ''}。原记录保留可查，作废后不再计入；理由必填。`} actionLabel="确认作废"
               confirmDisabled={!voidReason.trim()} onConfirm={runVoid}>
  <Field label="作废理由" id={`${uid}-void-reason`}>
    {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} bind:value={voidReason} />{/snippet}
  </Field>
  {#if actionError}<DataBlock state="error" message={actionError} />{/if}
</ConfirmDialog>
