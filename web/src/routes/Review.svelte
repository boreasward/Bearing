<script lang="ts">
  import { onMount } from 'svelte'
  import { fmtPct } from '../lib/format'
  import * as wb from '../lib/workbench'
  import { listAccounts, REAL_ACCOUNT_ID, type Account } from '$lib/accounts'
  import * as api from '../lib/api'
  import ConceptTerm from '../lib/components/ConceptTerm.svelte'
  import ReviewCard from '../lib/components/ReviewCard.svelte'
  import ReviewCardList from '../lib/components/ReviewCardList.svelte'
  import { ATTACH } from '../lib/knowledge'
  import { actualRText, knowHref } from '../lib/growth'
  import type { WeeklyLearning } from '../lib/growth'
  import { Alert, AlertDescription } from '$lib/ui/alert/index'
  import { Button } from '$lib/ui/button/index'
  import * as Card from '$lib/ui/card/index'
  import { DataTable } from '$lib/ui/data-table/index'
  import type { Column } from '$lib/ui/data-table/types'
  import CalcVersion from '$lib/ui/domain/CalcVersion.svelte'
  import CutoffTime from '$lib/ui/domain/CutoffTime.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import PageHeader from '$lib/ui/domain/PageHeader.svelte'
  import SampleSize from '$lib/ui/domain/SampleSize.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import Field from '$lib/ui/field/Field.svelte'
  import { Input } from '$lib/ui/input/index'
  import * as Sheet from '$lib/ui/sheet/index'
  import * as Select from '$lib/ui/select/index'
  import TabNav from '$lib/ui/tab-nav/TabNav.svelte'
  import { Textarea } from '$lib/ui/textarea/index'
  import { notify } from '$lib/ui/toast'

  let { sub }: { sub?: string } = $props()
  const uid = $props.id()
  const TABS = [['cards', '复盘卡'], ['shadow', '对照组'], ['discipline', '纪律'], ['tracks', '轨道对比'], ['weekly', '周度复盘'],
                 ['monthly', '月度复盘'], ['backchecks', '上线前检查']] as const
  // sub is the path below #review/: `cards`, `cards/12`, `weekly` …
  const head = $derived(sub?.split('/')[0])
  let tab = $derived(TABS.some(([id]) => id === head) ? head! : 'cards')
  const cardId = $derived.by(() => {
    const m = /^cards\/(\d+)$/.exec(sub ?? '')
    return m ? Number(m[1]) : null
  })
  // 页签上的待审定数（v2.7 §4.7）；读不到（如迁移 027 未执行）就不显示
  let toReview = $state(0)
  async function loadCardCounts() {
    try { toReview = (await api.getReviewCards({}))?.counts?.to_review ?? 0 } catch { toReview = 0 }
  }
  $effect(() => { void sub; void loadCardCounts() })

  let error = $state('')
  let shadow = $state<wb.ShadowView | null>(null)
  let weekly = $state<wb.WeeklyReview[] | null>(null)
  let monthly = $state<wb.MonthlyReview[] | null>(null)
  let backchecks = $state<wb.Backcheck[] | null>(null)
  let tracks = $state<wb.TracksView | null>(null)
  let discipline = $state<wb.DisciplineView | null>(null)
  const accountFromHash = () => new URLSearchParams(location.hash.split('?')[1] ?? '').get('account') || REAL_ACCOUNT_ID
  let selectedAccount = $state(accountFromHash())
  let accountChoices = $state<Account[]>([])
  let accountChoiceError = $state('')
  const selectedAccountName = $derived(accountChoices.find(account => account.id === selectedAccount)?.name ?? (selectedAccount === REAL_ACCOUNT_ID ? '真实账户' : selectedAccount))
  let accountSequence = 0
  $effect(() => {
    if (tab !== 'discipline' && tab !== 'tracks') return
    const sequence = ++accountSequence
    void listAccounts(true).then(result => { if (sequence === accountSequence) { accountChoices = result.accounts ?? []; accountChoiceError = '' } })
      .catch(err => { if (sequence === accountSequence) accountChoiceError = wb.describeError(err) })
  })
  onMount(() => {
    const onHash = () => { if (location.hash.startsWith('#review')) selectedAccount = accountFromHash() }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  })
  function selectAccount(value: string) {
    if (!value) return
    selectedAccount = value
    history.replaceState(null, '', `#review/${tab}?account=${encodeURIComponent(value)}`)
  }
  let callDrawer = $state<{ open: boolean; calls: wb.AgentToolCall[]; call: wb.AgentToolCall | null }>({ open: false, calls: [], call: null })
  const closeCalls = () => { callDrawer = { open: false, calls: [], call: null } }
  let pushResults = $state<Record<string, string>>({})
  let pushes = $state<wb.ReviewPush[]>([])
  let backcheckNote = $state('')
  let backcheckError = $state('')

  function monday(d = new Date()): string {
    const copy = new Date(d)
    copy.setDate(copy.getDate() - ((copy.getDay() + 6) % 7))
    return copy.toISOString().slice(0, 10)
  }
  function summaryRows(view: wb.TracksView): { pool: string; track: string; s: wb.TrackStats }[] {
    const out: { pool: string; track: string; s: wb.TrackStats }[] = []
    const pools: [string, Record<'rule' | 'agent' | 'actual', wb.TrackStats>][] = [
      ['全部', view.summary.all], ['核心池', view.summary.core], ['扩展池', view.summary.extended]]
    for (const [pool, group] of pools)
      for (const [track, s] of [['规则', group.rule], ['Agent', group.agent], ['实际', group.actual]] as [string, wb.TrackStats][])
        out.push({ pool, track, s })
    return out
  }

  // The drawer is opened from code, so there is no trigger to give focus back to: remember the run button that opened it.
  let callOpener: HTMLElement | null = null
  function restoreCallFocus(event: Event) {
    event.preventDefault()
    callOpener?.focus()
    callOpener = null
  }
  async function openRun(runId: string) {
    const active = document.activeElement
    callOpener = active instanceof HTMLElement && active !== document.body ? active : null
    const detail = await wb.getAgentRun(runId)
    callDrawer = { open: true, calls: detail.tool_calls, call: detail.tool_calls[0] ?? null }
  }

  // §8.2: the latest recorded attempt (scheduled or manual) beside each card's push button
  async function loadPushes() {
    try { pushes = (await wb.getReviewPushes()).pushes ?? [] } catch { pushes = [] }
  }
  function lastPushText(kind: string, period: string): string {
    const p = pushes.find(x => x.kind === kind && x.period === period)   // newest first
    if (!p) return ''
    const outcome = p.sent ? '已推送' : '未推送：' + (p.reason ?? '未知原因')
    return `最近推送 ${wb.dateTime(p.created_at)} · ${outcome} · ${p.trigger_source === 'scheduled' ? '定时' : '手动'}`
  }

  async function push(kind: string, period: string) {
    const key = kind + ':' + period
    pushResults[key] = '推送中…'
    pushResults = pushResults
    try {
      const out = await wb.postReviewPush(kind as any, period)
      pushResults[key] = out.sent ? '已推送' : ('未推送：' + (out.reason ?? '未知原因'))
      if (out.sent) notify.success('已推送到 Discord')
      pushResults = pushResults
      loadPushes()
    } catch (e) {
      pushResults[key] = wb.describeError(e)
      pushResults = pushResults
    }
  }

  let weekStart = $state(monday())
  let weekNotes = $state('')
  let weekError = $state('')

  let loadSequence = 0
  async function load(which: string, accountId?: string) {
    const sequence = ++loadSequence
    error = ''
    try {
      if (which === 'cards') return
      if (which === 'shadow') { const value = await wb.getShadow(); if (sequence === loadSequence) shadow = value }
      else if (which === 'tracks') { tracks = null; const value = await wb.getTracks(undefined, undefined, accountId); if (sequence === loadSequence) tracks = value }
      else if (which === 'discipline') { discipline = null; const value = await wb.getDiscipline(accountId); if (sequence === loadSequence) discipline = value }
      else if (which === 'monthly') { const value = await wb.getMonthly(); if (sequence === loadSequence) { monthly = value.reviews; void loadPushes() } }
      else if (which === 'backchecks') { const value = await wb.getBackchecks(); if (sequence === loadSequence) backchecks = value.backchecks }
      else { const value = await wb.getWeekly(); if (sequence === loadSequence) { weekly = value.reviews; void loadPushes() } }
    } catch (err) { if (sequence === loadSequence) error = wb.describeError(err) }
  }
  $effect(() => { void load(tab, tab === 'discipline' || tab === 'tracks' ? selectedAccount : undefined) })

  async function runBackcheckNow() {
    backcheckError = ''; backcheckNote = ''
    try {
      const record = await wb.runBackcheck()
      backcheckNote = record.stats?.note ?? ''
      notify.success('历史检查已运行')
      await load('backchecks')
    } catch (e) {
      backcheckError = wb.describeError(e)
    }
  }

  let weekBusy = $state(false)
  async function generateWeekly(e: Event) {
    e.preventDefault()
    if (weekBusy) return
    weekError = ''
    weekBusy = true
    const head = weekly?.find(r => r.week_start === weekStart)
    try {
      await wb.postWeekly({ week_start: weekStart, notes: weekNotes, supersedes_id: head?.id ?? null })
      weekNotes = ''
      notify.success('已生成周度复盘')
      await load('weekly')
    } catch (err) {
      weekError = wb.describeError(err)
    } finally {
      weekBusy = false
    }
  }

  const monthForm = () => ({ month: '', notes: '', requestId: wb.newRequestId() })
  let monthFormState = $state(monthForm())
  let monthError = $state('')

  let monthBusy = $state(false)
  async function generateMonthly(e: Event) {
    e.preventDefault()
    if (monthBusy) return
    monthError = ''
    if (!/^\d{4}-\d{2}-01$/.test(monthFormState.month)) { monthError = '月份须为当月 1 日（YYYY-MM-01）'; return }
    const head = monthly?.find(r => r.month === monthFormState.month)
    monthBusy = true
    try {
      await wb.postMonthly({ month: monthFormState.month, notes: monthFormState.notes,
                             supersedes_id: head?.id ?? null })
      monthFormState = monthForm()
      notify.success('已生成月度复盘')
      await load('monthly')
    } catch (err) {
      monthError = wb.describeError(err)
    } finally {
      monthBusy = false
    }
  }

  const windowText = (w: wb.Window | undefined) =>
    !w || w.status === 'immature' ? '未成熟' : w.excess !== undefined ? wb.pct(w.excess) : `走势 ${wb.pct(w.move)}`


  // ---- tabs ----
  const tabItems = $derived(TABS.map(([id, label]) => ({
    href: `#review/${id}${id === 'discipline' || id === 'tracks' ? `?account=${encodeURIComponent(selectedAccount)}` : ''}`, label, current: tab === id, ...(id === 'cards' && toReview > 0 ? { badge: toReview, badgeLabel: `待审定 ${toReview} 张` } : {}),
  })))
  // review-8 counts executed / not executed units, older snapshots decided / undecided; no `units` = not computed
  const weeklyN = (r: wb.WeeklyReview): number | null => {
    const u = r.stats?.units
    if (!u) return null
    return wb.isExecutionReview(r.calc_version) ? (u.executed ?? 0) + (u.not_executed ?? 0) : (u.decided ?? 0) + (u.undecided ?? 0)
  }

  // ---- tables ----
  type ShadowUnit = wb.ShadowView['units'][number]
  type SummaryRow = { pool: string; track: string; s: wb.TrackStats }
  type TrackUnit = wb.TracksView['units'][number]
  type PoolRow = { pool: string; rule: string; row: { triggers: number; plans: number; expired: number; filled: number; r_mean: number | null; r_median: number | null } }
  type BackcheckRow = wb.Backcheck

  const windowColumn = (n: string): Column<ShadowUnit> => ({
    key: `w${n}`, header: `${n} 日`, align: 'num',
    value: u => u.outcome.status === 'pending_entry' ? '待入场' : windowText(u.outcome.windows[n]),
  })
  const shadowColumns = $derived<Column<ShadowUnit>[]>([
    { key: 'ticker', header: '标的' },
    { key: 'signal_date', header: '信号日' },
    { key: 'rules', header: '规则', value: u => u.rule_ids.map(wb.ruleLabel).join('、') },
    { key: 'action', header: '建议', value: u => wb.ACTION_LABELS[u.proposed_action] },
    ...(wb.isExecutionReview(shadow?.calc_version ?? '') ? [] : [{ key: 'decision', header: '决定', value: (u: ShadowUnit) => wb.CHOICE_LABELS[u.decision] }]),
    windowColumn('5'), windowColumn('10'), windowColumn('20'),
  ])

  const summaryColumns: Column<SummaryRow>[] = [
    { key: 'pool', header: '池', value: r => r.pool },
    { key: 'track', header: '轨道', value: r => r.track },
    { key: 'units', header: '单元', align: 'num', value: r => r.s.units },
    { key: 'determined', header: '已定', align: 'num', value: r => r.s.determined },
    { key: 'pending', header: '待定', align: 'num', value: r => r.s.pending },
    { key: 'missing', header: '缺失', align: 'num', value: r => r.s.missing },
    { key: 'mean', header: '均值', align: 'num', cell: meanCell },
    { key: 'median', header: '中位', align: 'num', cell: medianCell },
    { key: 'sum', header: '合计', align: 'num', cell: sumCell },
    { key: 'wins', header: '盈利', align: 'num', value: r => r.s.wins },
  ]
  const trackColumns: Column<TrackUnit>[] = [
    { key: 'ticker', header: '标的', cell: trackTickerCell },
    { key: 'signal_date', header: '信号日' },
    { key: 'pool', header: '池', value: u => (u.pool === 'core' ? '核心' : '扩展') },
    { key: 'rule', header: '规则', cell: trackRuleCell },
    { key: 'agent', header: 'Agent', cell: trackAgentCell },
    { key: 'actual', header: '实际', cell: trackActualCell },
  ]
  const poolColumns: Column<PoolRow>[] = [
    { key: 'pool', header: '池', value: r => (r.pool === 'core' ? '核心池' : '扩展池') },
    { key: 'rule', header: '规则', value: r => wb.ruleLabel(r.rule) },
    { key: 'triggers', header: '触发', align: 'num', value: r => r.row.triggers },
    { key: 'plans', header: '计划', align: 'num', value: r => r.row.plans },
    { key: 'expired', header: '过期', align: 'num', value: r => r.row.expired },
    { key: 'filled', header: '成交', align: 'num', value: r => r.row.filled },
    { key: 'r_mean', header: 'R 均值', align: 'num', cell: poolMeanCell },
    { key: 'r_median', header: 'R 中位', align: 'num', cell: poolMedianCell },
  ]
  const excessCell = (b: BackcheckRow, h: '5' | '10' | '20') => {
    const excess = b.stats?.all?.excess?.[h]
    return excess ? `n=${excess.n} · 均 ${fmtPct(excess.mean)} · 中 ${fmtPct(excess.median)} · 正 ${fmtPct(excess.positive_share)}` : '—'
  }
  const backcheckColumns: Column<BackcheckRow>[] = [
    { key: 'created_at', header: '时间', value: b => wb.dateTime(b.created_at) },
    { key: 'calc_version', header: '版本' },
    { key: 'tickers', header: '标的', value: b => (b.params?.tickers as string[] | undefined)?.join('、') ?? '当前观察池' },
    { key: 'triggers', header: '触发', align: 'num', value: b => b.stats?.all?.triggers ?? 0 },
    { key: 'filled', header: '成交', align: 'num', value: b => b.stats?.all?.filled ?? 0 },
    { key: 'e5', header: '超额 5 日', value: b => excessCell(b, '5') },
    { key: 'e10', header: '超额 10 日', value: b => excessCell(b, '10') },
    { key: 'e20', header: '超额 20 日', value: b => excessCell(b, '20') },
    { key: 'flags', header: '标记', value: b => (b.stats?.all?.flags ?? []).join('、') || '无' },
  ]
  const disciplineColumns: Column<wb.DisciplineTrade>[] = [
    { key: 'ticker', header: '标的' }, { key: 'track', header: '依据计划', value: row => row.track ? wb.KIND_TEXT[row.track] ?? row.track : '无计划' },
    { key: 'flags', header: '纪律标记', value: row => row.flags.map(flag => wb.FLAG_LABELS[flag] ?? flag).join('、') || '无' },
    { key: 'immature', header: '未成熟', value: row => row.immature.map(flag => wb.FLAG_LABELS[flag] ?? flag).join('、') || '无' },
    { key: 'r', header: '实际 R', align: 'num', value: row => wb.rText(row.r) },
  ]
</script>

{#snippet rStat(v: number | null)}{#if v == null}<StatusPill domain="block" value="not_computed" />{:else}{wb.rText(v)}{/if}{/snippet}
{#snippet meanCell(r: SummaryRow)}{@render rStat(r.s.mean)}{/snippet}
{#snippet medianCell(r: SummaryRow)}{@render rStat(r.s.median)}{/snippet}
{#snippet sumCell(r: SummaryRow)}{@render rStat(r.s.sum)}{/snippet}
{#snippet poolMeanCell(r: PoolRow)}{#if r.row.r_mean == null}<StatusPill domain="block" value="not_computed" />{:else}{r.row.r_mean}{/if}{/snippet}
{#snippet poolMedianCell(r: PoolRow)}{#if r.row.r_median == null}<StatusPill domain="block" value="not_computed" />{:else}{r.row.r_median}{/if}{/snippet}
{#snippet trackTickerCell(u: TrackUnit)}<a class="text-primary underline" href={`#instrument/${u.ticker}`}>{u.ticker}</a>{/snippet}
{#snippet trackRuleCell(u: TrackUnit)}{wb.TRACK_STATUS_TEXT_V2[u.rule.status]} {wb.rText(u.rule.r)}{#if u.rule.plan_id} · <a class="text-primary underline" href={`#plan/${u.rule.plan_id}`}>计划 {String(u.rule.plan_id).slice(0, 8)}</a>{/if}{/snippet}
{#snippet trackAgentCell(u: TrackUnit)}{wb.TRACK_STATUS_TEXT_V2[u.agent.status]} {wb.rText(u.agent.r)}{#if u.agent.run_id} · <Button variant="link" size="sm" onclick={() => openRun(u.agent.run_id!)}>{String(u.agent.run_id).slice(0, 8)}</Button>{/if}{/snippet}
{#snippet trackActualCell(u: TrackUnit)}{wb.TRACK_STATUS_TEXT_V2[u.actual.status]} {wb.rText(u.actual.r)}{#if u.actual.plan_kind} · 照{wb.KIND_TEXT[u.actual.plan_kind]}{/if}{/snippet}

<div class="grid gap-4">
  <PageHeader level={1} title="复盘">
    {#snippet meta()}描述性统计：股票少、样本重叠，不代表任何规则有效。{/snippet}
  </PageHeader>
  <TabNav label="复盘页签" items={tabItems} />
  {#if tab === 'discipline' || tab === 'tracks'}
    <Field label="复盘账户" id={`${uid}-account`}>
      {#snippet children({ id, describedby })}
        <Select.Root type="single" value={selectedAccount} onValueChange={selectAccount}>
          <Select.Trigger {id} aria-describedby={describedby}>{selectedAccountName}</Select.Trigger>
          <Select.Content>
            {#if !accountChoices.length}<Select.Item value={REAL_ACCOUNT_ID} label="真实账户" />{/if}
            {#each accountChoices as account (account.id)}<Select.Item value={account.id} label={account.name} />{/each}
          </Select.Content>
        </Select.Root>
      {/snippet}
    </Field>
    {#if accountChoiceError}<DataBlock state="degraded" message={`账户列表暂不可用：${accountChoiceError}`} />{/if}
  {/if}

  {#if error}<DataBlock state="error" message={error} />{/if}

  {#if tab === 'cards'}
    {#if cardId !== null}
      <ReviewCard id={cardId} />
    {:else}
      <ReviewCardList oncounts={counts => toReview = counts.to_review ?? 0} />
    {/if}
  {/if}

  {#if tab === 'shadow' && shadow && !error}
    {@const allUnits = wb.isExecutionReview(shadow.calc_version)}
    <div class="grid gap-1">
      <div class="flex flex-wrap items-center gap-3" data-testid="stats-header">
        <CalcVersion version={shadow.calc_version} />
        <SampleSize n={shadow.units.length} />
      </div>
      <p class="text-xs text-muted-foreground">假设下一交易日开盘入场；数值为相对 SPY 的超额收益，卖出取反（避开的跌幅）。{allUnits ? '范围是全部信号单元（不论你有没有成交）。' : ''}自 {shadow.since} 起。</p>
    </div>
    {#if shadow.units.length}
      <DataTable columns={shadowColumns} rows={shadow.units} rowKey={u => u.id} ariaLabel="对照组" sticky={false} />
    {:else}
      <DataBlock state="empty" message={allUnits ? '暂无信号单元' : '暂无已决定的信号'} />
    {/if}
  {/if}

  {#if tab === 'tracks' && !error}
    {#if tracks}
      <div class="grid gap-1">
        <div class="flex flex-wrap items-center gap-3" data-testid="stats-header">
          <CalcVersion version={tracks.calc_version} />
          <SampleSize n={tracks.units.length} />
        </div>
        <p class="text-xs text-muted-foreground">同一批买入单元的三条轨道（R 按各自计划的止损计）；自 {tracks.since} 至 {tracks.until}。</p>
      </div>
      <DataTable columns={summaryColumns} rows={summaryRows(tracks)} rowKey={r => `${r.pool}|${r.track}`} ariaLabel="轨道汇总" sticky={false} />
      {#if tracks.units.length}
        <DataTable columns={trackColumns} rows={tracks.units} rowKey={u => u.ticker + u.signal_date} ariaLabel="轨道对比" sticky={false} />
      {:else}
        <DataBlock state="empty" message="窗口内没有可比单元" />
      {/if}
    {:else}
      <DataBlock state="loading" skeleton="table" />
    {/if}
  {/if}

  {#if tab === 'discipline' && !error}
    <Section level={2} title="执行纪律">
      <DataBlock state={!discipline ? 'loading' : 'ready'}>
        {#if discipline}
          <div class="flex flex-wrap items-center gap-3 text-xs text-muted-foreground"><CalcVersion version={discipline.calc_version ?? 'discipline-2'} /><SampleSize n={discipline.trades.length} /><CutoffTime at={discipline.cutoff_at} /><span>数据日 {discipline.session}</span></div>
          <p class="text-sm">计划买入 {discipline.planned_buys} · 遵守率 {discipline.compliance_rate == null ? '—' : wb.pct(discipline.compliance_rate)}</p>
          <p class="text-sm">计划外 {discipline.counts.unplanned} · 追高 {discipline.counts.chase} · 过早止盈 {discipline.counts.early_take} · 死扛亏损 {discipline.counts.held_loser}</p>
          <DataBlock state={discipline.trades.length ? 'ready' : 'empty'} message="该账户暂无可评估的买入成交"><DataTable columns={disciplineColumns} rows={discipline.trades} rowKey={row => row.trade_id} ariaLabel="执行纪律明细" /></DataBlock>
        {/if}
      </DataBlock>
    </Section>
  {/if}

  {#if tab === 'weekly' && weekly && !error}
    <Card.Root size="sm" class="px-3">
      <form class="flex flex-wrap items-end gap-3 text-sm" onsubmit={generateWeekly}>
        <Field label="周一" id={`${uid}-week`}>
          {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} type="date" bind:value={weekStart} />{/snippet}
        </Field>
        <div class="min-w-48 flex-1">
          <Field label="复盘笔记" id={`${uid}-week-notes`}>
            {#snippet children({ id, describedby })}<Textarea {id} aria-describedby={describedby} rows={2} bind:value={weekNotes} />{/snippet}
          </Field>
        </div>
        <Button type="submit" variant="primary" busy={weekBusy}>生成复盘</Button>
        {#if weekError}<Alert variant="danger" class="w-auto py-1"><AlertDescription class="text-xs">{weekError}</AlertDescription></Alert>{/if}
      </form>
    </Card.Root>
    {#each weekly as r (r.id)}
      <Card.Root role="article" size="sm" class="gap-2 px-3 text-sm">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="text-base font-medium">{r.stats.week?.start ?? r.week_start} 周 ·
            {#if wb.isExecutionReview(r.calc_version)}已执行 {r.stats.units.executed} · 未执行 {r.stats.units?.not_executed ?? 0}
            {:else}已决定 {r.stats.units?.decided ?? 0} · 未处理 {r.stats.units?.undecided ?? 0}{/if}</h2>
          <div class="flex flex-wrap items-center gap-2">
            <Button size="sm" onclick={() => push('weekly', r.week_start)}>推送到 Discord</Button>
            {#if pushResults['weekly:' + r.week_start]}<span class="text-xs text-muted-foreground">{pushResults['weekly:' + r.week_start]}</span>
            {:else if lastPushText('weekly', r.week_start)}<span class="text-xs text-muted-foreground">{lastPushText('weekly', r.week_start)}</span>{/if}
          </div>
        </div>
        <div class="flex flex-wrap items-center gap-3" data-testid="stats-header">
          <CalcVersion version={r.calc_version} />
          <SampleSize n={weeklyN(r)} />
          <span class="text-xs text-muted-foreground"><CutoffTime at={r.created_at} label="生成于" />{r.supersedes_id ? ' · 更正版' : ''}</span>
        </div>
        {#if r.stats.note}<p class="text-xs text-muted-foreground">{r.stats.note}</p>{/if}
        {#if r.notes}<p>{r.notes}</p>{/if}
        {#each [['按规则', r.stats.by_rule], wb.isExecutionReview(r.calc_version) ? ['按执行', r.stats.by_execution] : ['按决定', r.stats.by_decision]] as [title, groups]}
          {#if groups && Object.keys(groups).length}
            <p class="text-xs text-muted-foreground">{title}（超额收益：均值 / 中位数 · 成熟数/总数）</p>
            <ul>
              {#each Object.entries(groups) as [name, windows]}
                <li>{title === '按规则' ? wb.ruleLabel(name) : title === '按执行' ? (wb.executionText(name) || name) : wb.CHOICE_LABELS[name as wb.Choice] ?? name}：
                  {#each ['5', '10', '20'] as n}{@const s = (windows as any)[n]}
                    {n} 日 {wb.pct(s?.mean)} / {wb.pct(s?.median)}（{s?.mature ?? 0}/{s?.count ?? 0}）{' '}
                  {/each}
                </li>
              {/each}
            </ul>
          {/if}
        {/each}
        {#if r.stats.by_pool && Object.keys(r.stats.by_pool).length}
          <p class="text-xs text-muted-foreground">分池视角（触发 / 计划 / 过期 / 成交 / R 均值 / 中位数）</p>
          <DataTable columns={poolColumns} sticky={false} ariaLabel="分池视角" rowKey={row => `${row.pool}|${row.rule}`}
                     rows={Object.entries(r.stats.by_pool).flatMap(([pool, rules]) =>
                       Object.entries(rules as Record<string, PoolRow['row']>).map(([rule, row]) => ({ pool, rule, row })))} />
        {/if}
        {#if r.stats.execution?.length}
          <p class="text-xs text-muted-foreground">执行偏差</p>
          <ul>{#each r.stats.execution as x}<li>{x.ticker} 成交价较 T+1 开盘 {wb.pct(x.slippage)} · {#if x.delay_hours == null}自定计划，不计延迟{:else}{wb.isExecutionReview(r.calc_version) ? '版本生成后' : '决定后'} {Number(x.delay_hours).toFixed(1)} 小时成交{/if}</li>{/each}</ul>
        {/if}
        {#if wb.isExecutionReview(r.calc_version)}
          {@const unplanned = r.stats.unplanned_trades ?? r.stats.unsignaled_trades}
          {#if unplanned?.length}<p>计划外成交 {unplanned.length} 笔</p>{/if}
        {:else if r.stats.unsignaled_trades?.length}<p>无信号交易 {r.stats.unsignaled_trades.length} 笔</p>{/if}
        {#if r.stats.plans && Object.keys(r.stats.plans).length}
          <p class="text-xs text-muted-foreground">计划影子（数量 / 已过期 / R 均值 / 中位数）</p>
          <ul>{#each Object.entries(r.stats.plans as Record<string, { count: number; expired: number; r_mean: number | null; r_median: number | null }>) as [kind, p]}
            <li>{wb.planKindText(kind, r.calc_version)}：{p.count} · 已过期 {p.expired} · R {#if p.r_mean != null}{p.r_mean}{:else}<StatusPill domain="block" value="not_computed" />{/if} / {#if p.r_median != null}{p.r_median}{:else}<StatusPill domain="block" value="not_computed" />{/if}</li>
          {/each}</ul>
        {/if}
        {#if r.stats.tracks}
          <p class="text-xs text-muted-foreground">轨道对比（单元 / 已定 / 均值 / 中位 / 合计）</p>
          <ul>
            {#each [['规则', r.stats.tracks.summary.all.rule], ['Agent', r.stats.tracks.summary.all.agent], ['实际', r.stats.tracks.summary.all.actual]] as [track, s]}
              <li>{track}：{s.units} 单元 · 已定 {s.determined} · 均值 {wb.rText(s.mean)} · 中位 {wb.rText(s.median)} · 合计 {wb.rText(s.sum)}</li>
            {/each}
          </ul>
        {:else}
          <p class="text-xs text-muted-foreground">轨道对比：该快照无轨道对比</p>
        {/if}
        {#if r.stats.discipline}
          {@const d = r.stats.discipline}
          <p class="text-xs text-muted-foreground">纪律</p>
          <p>计划内买入 {d.planned_buys ?? 0} · 遵守率 {#if d.compliance_rate == null}<StatusPill domain="block" value="not_computed" />{:else}{`${Math.round(d.compliance_rate * 100)}%`}{/if}</p>
          <p class="text-xs text-muted-foreground">追高 {d.counts?.chase ?? 0} · 过早止盈 {d.counts?.early_take ?? 0} · 死扛亏损 {d.counts?.held_loser ?? 0} · 计划外 {d.counts?.unplanned ?? 0}</p>
        {/if}
        {#if r.stats.learning}
          {@const L = r.stats.learning as WeeklyLearning}
          <section aria-label="你学到了什么" class="grid gap-1 border-t border-border pt-2">
            <h3 class="font-medium">你学到了什么 <span class="text-xs font-normal"><ConceptTerm id={ATTACH.weeklyLearning.learning}>说明</ConceptTerm></span></h3>
            {#if L.discipline.cards === 0 && L.cards.length === 0 && L.knowledge.length === 0}
              <p>本周没有结束的交易，也没有被检验的知识</p>
            {:else}
              <p>纪律（本周结束的 {L.discipline.cards} 笔）：追高 {L.discipline.chase} · 过早止盈 {L.discipline.early_take} · 死扛亏损 {L.discipline.held_loser} · 计划外 {L.discipline.unplanned} · 待定 {L.discipline.pending}</p>
              <p>复盘卡 {L.counts.total} 张（已审定 {L.counts.reviewed} · 待审定 {L.counts.to_review}）</p>
              <ul class="grid gap-0.5 text-xs">
                {#each L.cards as c (c.card_id)}
                  <li><a class="text-primary underline" href={`#review/cards/${c.card_id}`}>{c.ticker} {c.end_text}</a>
                    · 实际 {actualRText(c)} · {wb.isExecutionReview(r.calc_version) ? (c.execution_text ?? c.decision_text) : c.decision_text}{c.headline ? ` · ${c.headline}` : ''}</li>
                {/each}
              </ul>
              <p>被检验的知识 {L.knowledge.length} 条</p>
              <ul class="grid gap-0.5 text-xs">
                {#each L.knowledge as k (k.ref)}
                  <li><a class="text-primary underline" href={knowHref(k.ref)}>{k.ref}</a> {k.title}
                    · 样本 {k.sample.count}（{k.sample.r_mean === null ? '少于 5 个不给均值' : `均值 ${wb.rText(k.sample.r_mean)}`}）</li>
                {/each}
              </ul>
            {/if}
          </section>
        {:else}
          <p class="text-xs text-muted-foreground">该快照没有『你学到了什么』（review-6 及更早）</p>
        {/if}
      </Card.Root>
    {:else}<DataBlock state="empty" message="尚无周度复盘" />{/each}
  {/if}

  {#if tab === 'monthly' && !error}
    <Card.Root size="sm" class="px-3">
      <form class="flex flex-wrap items-end gap-3 text-sm" onsubmit={generateMonthly}>
        <Field label="月份（当月 1 日）" id={`${uid}-month`}>
          {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} aria-label="月份（当月 1 日）" type="date" bind:value={monthFormState.month} />{/snippet}
        </Field>
        <div class="min-w-48 flex-1">
          <Field label="复盘笔记" id={`${uid}-month-notes`}>
            {#snippet children({ id, describedby })}<Textarea {id} aria-describedby={describedby} aria-label="月度复盘笔记" rows={2} bind:value={monthFormState.notes} />{/snippet}
          </Field>
        </div>
        <Button type="submit" variant="primary" busy={monthBusy}>生成月度复盘</Button>
        {#if monthError}<Alert variant="danger" class="w-auto py-1"><AlertDescription class="text-xs">{monthError}</AlertDescription></Alert>{/if}
      </form>
    </Card.Root>
    {#each monthly ?? [] as r (r.id)}
      <Card.Root role="article" size="sm" class="gap-2 px-3 text-sm">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="text-base font-medium">{r.month} · {(r.stats.tier && wb.TIER_LABELS[r.stats.tier]) || '缺数据'}</h2>
          <div class="flex flex-wrap items-center gap-2">
            <Button size="sm" onclick={() => push('monthly', r.month)}>推送到 Discord</Button>
            {#if pushResults['monthly:' + r.month]}<span class="text-xs text-muted-foreground">{pushResults['monthly:' + r.month]}</span>
            {:else if lastPushText('monthly', r.month)}<span class="text-xs text-muted-foreground">{lastPushText('monthly', r.month)}</span>{/if}
          </div>
        </div>
        <div class="flex flex-wrap items-center gap-3" data-testid="stats-header">
          <CalcVersion version={r.calc_version} />
          <span class="text-xs text-muted-foreground"><CutoffTime at={r.created_at} label="生成于" />{r.supersedes_id ? ' · 更正版' : ''}</span>
        </div>
        {#if r.stats.return?.status === 'ok'}
          <p>月度收益 {wb.pct(r.stats.return.return)} · 净出入金 {#if r.stats.return.net_flow != null}{r.stats.return.net_flow}{:else}<StatusPill domain="block" value="not_computed" />{/if}</p>
          {#if r.stats.spy}<p>SPY {wb.pct(r.stats.spy.return)} · 超额 {wb.pct(r.stats.spy.excess)}</p>{/if}
        {:else}
          <DataBlock state="not_computed" message="缺资金快照，无法计算" />
        {/if}
        {#if r.stats.review_flag === 'review'}<Alert variant="warn"><AlertDescription>流程复查：连续 3 个月亏损且跑输 SPY</AlertDescription></Alert>
        {:else if r.stats.review_flag === 'insufficient'}<p class="text-xs text-muted-foreground">流程复查：样本不足（不足 3 个月）</p>{/if}
        {#if r.stats.review}
          {@const rv = r.stats.review}
          <p class="text-xs text-muted-foreground">复查明细</p>
          <ul class="text-xs">
            {#each rv.months ?? [] as m}
              <li>{String(m.month).slice(0, 7)}：本月 {wb.pct(m.return)} · SPY {wb.pct(m.spy_return)}</li>
            {/each}
          </ul>
          <p class="text-xs">3 个月复合 {wb.pct(rv.compound)} · SPY {wb.pct(rv.spy_compound)}</p>
        {/if}
        {#if r.stats.sensitivity?.rows?.length}
          <p class="text-xs text-muted-foreground">敏感性（近似）</p>
          <ul class="text-xs">{#each r.stats.sensitivity.rows as row}
            <li>风险 {wb.pct(row.risk_pct)}：收益 ≈ {wb.pct(row.return)} · 最大回撤 ≈ {wb.pct(row.max_drawdown)}</li>
          {/each}</ul>
        {/if}
        {#if r.stats.plans && Object.keys(r.stats.plans).length}
          <p class="text-xs text-muted-foreground">计划影子</p>
          <ul>{#each Object.entries(r.stats.plans) as [kind, p]}
            <li>{wb.planKindText(kind, r.calc_version)}：{p.count} · 已过期 {p.expired} · R {#if p.r_mean != null}{p.r_mean}{:else}<StatusPill domain="block" value="not_computed" />{/if} / {#if p.r_median != null}{p.r_median}{:else}<StatusPill domain="block" value="not_computed" />{/if}</li>
          {/each}</ul>
        {/if}
        {#if r.stats.discipline}
          <p class="text-xs text-muted-foreground">纪律 · 遵守率 {#if r.stats.discipline.compliance_rate == null}<StatusPill domain="block" value="not_computed" />{:else}{`${Math.round(r.stats.discipline.compliance_rate * 100)}%`}{/if}</p>
          <p class="text-xs text-muted-foreground">追高 {r.stats.discipline.counts?.chase ?? 0} · 过早止盈 {r.stats.discipline.counts?.early_take ?? 0} · 死扛亏损 {r.stats.discipline.counts?.held_loser ?? 0} · 计划外 {r.stats.discipline.counts?.unplanned ?? 0}</p>
        {/if}
        {#if r.notes}<p>{r.notes}</p>{/if}
      </Card.Root>
    {:else}<DataBlock state="empty" message="尚无月度复盘" />{/each}
  {/if}

  {#if tab === 'backchecks' && !error}
    <Section level={2} title="上线前检查 · 历史回放">
      <p class="text-xs text-muted-foreground">新规则上线前手动运行，回答「触发是否过频、止损是否总被扫掉」。只读已存 K 线与 SEC 文件，不联网、不补采。也可用命令行：python -m investment_assistant.optimize.backcheck。</p>
      <div class="flex flex-wrap items-center gap-2">
        <Button variant="primary" onclick={runBackcheckNow}>运行历史检查</Button>
        {#if backcheckError}<Alert variant="danger" class="w-auto py-1"><AlertDescription class="text-xs">{backcheckError}</AlertDescription></Alert>{/if}
      </div>
      {#if backcheckNote}<p class="text-xs text-muted-foreground">{backcheckNote}</p>{/if}
      {#if backchecks?.length}
        <div class="overflow-x-auto">
          <DataTable columns={backcheckColumns} rows={backchecks} rowKey={b => b.id} ariaLabel="历史检查" sticky={false} />
        </div>
        {#each backchecks as b (b.id)}
          <small class="block text-xs text-muted-foreground">{b.stats?.note}</small>
        {/each}
      {:else if backchecks}
        <DataBlock state="empty" message="尚无历史检查记录" />
      {/if}
    </Section>
  {/if}

  <Sheet.Root open={callDrawer.open} onOpenChange={open => { if (!open) closeCalls() }}>
    <Sheet.Content side="right" width="sm" onCloseAutoFocus={restoreCallFocus}>
      <Sheet.Header>
        <Sheet.Title>工具调用{#if callDrawer.call} call:{callDrawer.call.seq}{/if}</Sheet.Title>
        <Sheet.Description class="sr-only">这次 Agent 运行里的工具调用</Sheet.Description>
      </Sheet.Header>
      <div class="grid gap-2 px-4 pb-4">
        {#if callDrawer.calls.length}
          <p class="flex flex-wrap gap-2 text-xs">{#each callDrawer.calls as tc}<Button variant="link" size="sm" class="text-xs" onclick={() => callDrawer = { ...callDrawer, call: tc }}>call:{tc.seq}</Button>{/each}</p>
        {/if}
        {#if callDrawer.call}
          {@const c = callDrawer.call}
          <p class="text-xs text-muted-foreground">工具 {c.tool} · 阶段 {c.phase === 'propose' ? '建议' : '反方'} · {wb.dateTime(c.called_at)}</p>
          <p class="break-all text-xs">参数 {JSON.stringify(c.args)}</p>
          <p class="break-all text-xs">哈希 {c.result_sha256.slice(0, 8)}</p>
          <pre class="mt-2 whitespace-pre-wrap break-all text-xs">{JSON.stringify(c.result, null, 2)}</pre>
        {:else}
          <p class="text-sm text-muted-foreground">未找到该编号的工具调用</p>
        {/if}
      </div>
    </Sheet.Content>
  </Sheet.Root>
</div>
