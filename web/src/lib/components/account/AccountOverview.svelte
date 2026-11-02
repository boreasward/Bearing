<script lang="ts">
  import type { EChartsOption } from 'echarts'
  import type { Account, AccountDetail, EquityHistory, EquityRow } from '$lib/accounts'
  import { accountMarkLine, assessEquityHistory, mergeSpyHistory, normalizedEquity, valuationWarning } from '$lib/accounts'
  import Markers from './Markers.svelte'
  import { pct } from '$lib/workbench'
  import EChart from '$lib/charts/EChart.svelte'
  import { Button } from '$lib/ui/button/index'
  import CalcVersion from '$lib/ui/domain/CalcVersion.svelte'
  import CutoffTime from '$lib/ui/domain/CutoffTime.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import Section from '$lib/ui/domain/Section.svelte'

  let { detail, history, historyError = '', monthReturn, monthReturnNote, spyReturn, otherHistories = [] }: {
    detail: AccountDetail; history: EquityHistory | null; monthReturn?: string | null; monthReturnNote?: string | null; spyReturn?: string | null
    historyError?: string; otherHistories?: { account: Pick<Account, 'id' | 'name' | 'opened_on' | 'opening_session'>; history: EquityHistory }[]
  } = $props()
  let showSPY = $state(true)
  let showOthers = $state(false)
  const equity = $derived(detail.equity)
  const openingSession = $derived(detail.account.opening_session ?? detail.account.opened_on)
  const cutoff = $derived(detail.cutoff_at ?? equity?.computed_at)
  const warning = $derived(valuationWarning(equity?.detail) || valuationWarning(history?.rows.find(row => row.session === equity?.session)?.detail))
  const ownCurve = $derived(assessEquityHistory(history?.rows ?? [], openingSession))
  const otherCurves = $derived(otherHistories.map(item => ({ ...item, curve: assessEquityHistory(item.history.rows, item.account.opening_session ?? item.account.opened_on ?? null) })))
  function qualityKey(row: EquityRow): string {
    const detail = row.detail ?? {}
    return JSON.stringify([
      Array.isArray(detail.bars_missing) ? detail.bars_missing.map(String).sort() : [],
      detail.stale && typeof detail.stale === 'object' ? Object.entries(detail.stale).map(([ticker, date]) => [ticker, String(date)]).sort(([a], [b]) => a.localeCompare(b)) : [],
    ])
  }
  function sameEquityVersion(current: EquityRow, historical: EquityRow): boolean {
    if (current.id != null && historical.id != null) return String(current.id) === String(historical.id)
    const fields = ['cash', 'positions_value', 'equity', 'realized_pnl_cum', 'unrealized_pnl', 'net_flows_cum', 'day_pnl', 'positions_count', 'account_version', 'run_id'] as const
    return !!current.computed_at && current.computed_at === historical.computed_at
      && fields.every(field => String(current[field] ?? '') === String(historical[field] ?? ''))
      && qualityKey(current) === qualityKey(historical)
  }
  const dayPnlWarning = $derived.by(() => {
    if (!equity) return ''
    if (historyError) return `${equity.session} · 今日盈亏未核验：权益历史读取失败，${historyError}`
    if (!history) return `${equity.session} · 正在读取权益历史，尚未核验今日盈亏所依赖的前后两行`
    const rows = [...history.rows].sort((a, b) => a.session.localeCompare(b.session))
    const index = rows.findIndex(row => row.session === equity.session)
    if (index < 0) return `${equity.session} · 未找到当日权益行，无法核验今日盈亏所依赖的前后两行`
    if (!sameEquityVersion(equity, rows[index])) return `${equity.session} · 数据版本不同，刷新后核验今日盈亏`
    const previous = rows[index - 1]
    if (!previous) return equity.session === openingSession
      ? `首个交易日 ${equity.session} 无上一权益行，今日盈亏不适用`
      : `${equity.session} · 无法确认上一权益行，今日盈亏未核验`
    const reasons = [
      ...(warning ? [`${equity.session}：${warning}`] : []),
      ...ownCurve.issues.filter(issue => issue.session === previous.session || (issue.session === equity.session && !warning)).map(issue => `${issue.session}：${issue.reason}`),
    ]
    return reasons.length ? `今日盈亏暂不可用 · ${reasons.join('；')}` : ''
  })
  const curveWarnings = $derived([
    ...ownCurve.issues.map(issue => `${detail.account.name} · ${issue.session} · ${issue.reason}`),
    ...(ownCurve.baselineWarning ? [`${detail.account.name} · ${ownCurve.baselineWarning}`] : []),
    ...(showOthers ? otherCurves.flatMap(item => [...item.curve.issues.map(issue => `${item.account.name} · ${issue.session} · ${issue.reason}`), ...(item.curve.baselineWarning ? [`${item.account.name} · ${item.curve.baselineWarning}`] : [])]) : []),
  ])
  const spy = $derived(mergeSpyHistory([...(history ? [history] : []), ...otherHistories.map(item => item.history)]))
  const returnsIncomplete = $derived(!!warning || ownCurve.issues.length > 0 || !!ownCurve.baselineWarning)
  const monthNoteText = $derived(monthReturnNote === 'snapshot' ? '来源：券商资金快照' : monthReturnNote === 'ledger' ? '来源：账户权益账本' : monthReturnNote)
  const drawdown = $derived(detail.account.max_drawdown)
  const metrics = $derived([
    { label: '权益', value: warning ? null : equity?.equity }, { label: '现金', value: equity?.cash },
    { label: '持仓市值', value: warning ? null : equity?.positions_value }, { label: '今日盈亏', value: dayPnlWarning ? null : equity?.day_pnl },
    { label: '本月收益率', value: returnsIncomplete ? null : monthReturn }, { label: '最大回撤', value: returnsIncomplete || drawdown?.pct == null ? null : pct(Number(drawdown.pct)) },
  ])
  const option = $derived<EChartsOption>({
    tooltip: { trigger: 'axis' }, legend: { type: 'scroll' }, grid: { left: 50, right: 20, top: 50, bottom: 35 },
    xAxis: { type: 'time' }, yAxis: { type: 'value', scale: true, name: '首个交易日 = 100' },
    series: [
      { name: detail.account.name, type: 'line', showSymbol: false, connectNulls: false, data: ownCurve.data, markLine: accountMarkLine(history?.markers) },
      ...(showSPY && spy.length ? [{ name: 'SPY', type: 'line' as const, showSymbol: false, data: normalizedEquity(spy.map(row => ({ session: row.session, equity: row.close }))) }] : []),
      ...(showOthers ? otherCurves.map(({ account, curve }) => ({ name: account.name, type: 'line' as const, showSymbol: false, connectNulls: false, data: curve.data })) : []),
    ],
  })
</script>

<Section level={2} title="账户概览">
  <div class="flex flex-wrap items-center gap-3 text-xs text-muted-foreground"><CalcVersion version="account-1" /><CutoffTime at={cutoff} />{#if detail.account.kind === 'paper'}<CalcVersion version="paper-fill-1" />{/if}</div>
  {#if !equity}<DataBlock state="not_computed" message="尚无结算权益，等待开账及交易日结算；已有资金快照与成交仍可查看。" />{/if}
  <DataBlock state={warning ? 'degraded' : 'ready'} message={warning ? `${equity?.session ?? '日期未提供'} · ${warning}` : undefined}>
    <div class="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {#each metrics as metric, index}
        <div class="grid min-w-0 content-start gap-1 rounded-md border border-border bg-card p-3" data-account-metric>
          <span class="text-xs text-muted-foreground">{metric.label}</span>
          <span class="text-lg"><MetricValue value={null} raw={metric.value == null ? null : String(metric.value)} tone={index === 3 ? 'updown' : 'none'} /></span>
          {#if (warning && [0, 2].includes(index)) || (returnsIncomplete && [4, 5].includes(index))}<span class="text-xs text-warn">估值不完整</span>{/if}
          {#if index === 3 && dayPnlWarning}<span class="text-xs text-warn">{dayPnlWarning}</span>{/if}
          {#if index === 4}
            <span class="text-xs text-muted-foreground">同月 SPY {spyReturn ?? '—'}</span>
            {#if monthNoteText}<span class="text-xs text-muted-foreground">{monthNoteText}</span>{/if}
          {/if}
          {#if index === 5}<span class="text-xs text-muted-foreground">{drawdown?.peak_session ?? '—'} → {drawdown?.trough_session ?? '—'}</span>{/if}
        </div>
      {/each}
    </div>
  </DataBlock>
</Section>

<Section level={2} title="权益曲线">
  <div class="flex flex-wrap items-center gap-3 text-xs text-muted-foreground"><CalcVersion version="account-1" /><CutoffTime at={cutoff} /></div>
  <div class="flex flex-wrap items-center gap-2">
    <Button size="sm" variant={showSPY ? 'outline' : 'ghost'} aria-pressed={showSPY} onclick={() => showSPY = !showSPY}>叠加 SPY</Button>
    <Button size="sm" variant={showOthers ? 'outline' : 'ghost'} aria-pressed={showOthers} onclick={() => showOthers = !showOthers}>叠加其他账户</Button>
    <span class="text-xs text-muted-foreground">各账户首个交易日 = 100，首个交易日不同，收益区间不同。当前账户开账日：{detail.account.opened_on ?? '未开账'}；首个交易日：{openingSession ?? '未开账'}。</span>
  </div>
  {#each curveWarnings as message}<DataBlock state="degraded" {message} />{/each}
  <p class="text-xs text-muted-foreground">首个交易日为开账当日或之后的首个交易日。缺失或沿用旧收盘的日期留空，不连接缺口；首个交易日缺少完整权益时不绘制该账户曲线。</p>
  <DataBlock state={historyError ? 'error' : !history ? 'loading' : history.rows.length ? 'ready' : 'empty'} message={historyError || '尚无每日权益，结算后显示曲线'}>
    {#key `${showSPY}-${showOthers}`}<div class="min-w-0" role="img" aria-label="归一化账户权益曲线"><EChart {option} /></div>{/key}
  </DataBlock>
</Section>
<Markers markers={history?.markers} />
