<script lang="ts">
  import { onMount } from 'svelte'
  import { Alert, AlertDescription } from '$lib/ui/alert/index'
  import { Button } from '$lib/ui/button/index'
  import * as Card from '$lib/ui/card/index'
  import { DataTable } from '$lib/ui/data-table/index'
  import type { Column } from '$lib/ui/data-table/types'
  import CalcVersion from '$lib/ui/domain/CalcVersion.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import PageHeader from '$lib/ui/domain/PageHeader.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import { Input } from '$lib/ui/input/index'
  import { notify } from '$lib/ui/toast'
  import PlanTable from '../lib/components/PlanTable.svelte'
  import RunOverview from '../lib/components/RunOverview.svelte'
  import PortfolioCheck from '../lib/components/today/PortfolioCheck.svelte'
  import * as api from '../lib/api'
  import * as tl from '../lib/timeline'
  import * as wb from '../lib/workbench'

  // 「今日」（spec §10.1）：运行一览 → 筛选 → 市场环境 → 计划表 → 其他触发。
  // 计划表自己取数（GET /plan-table，可选日期与扩展池），不依赖批处理视图；没有任何决定按钮。
  let view = $state<wb.TodayView | null>(null)
  let error = $state('')
  let loading = $state(true)
  let running = $state(false)
  let pushing = $state(false)
  let generation = 0
  let screenExpanded = $state(false)
  let screenFull = $state<wb.ScreenReport | null>(null)
  let screenError = $state('')

  // 计划表：session 为空 = 后端缺省（最近一个已收盘交易日）
  let table = $state<wb.PlanTableView | null>(null)
  let tableError = $state('')
  let tableLoading = $state(true)
  let session = $state('')
  let includeExtended = $state(false)
  let tableGeneration = 0
  let rechecks = $state<Record<string, { busy: boolean; text: string; since?: number }>>({})

  async function expandScreen() {
    if (!view || !view.screen) return
    screenExpanded = true
    screenError = ''
    try {
      const out = await wb.getScreen(view.session, true)
      screenFull = out.report
    } catch (e) {
      screenError = String((e as Error)?.message ?? e)
    }
  }

  const REVIEW_FALLBACK: Record<string, string> = { conflict: '冲突 / 回避', watch: '等待触发' }

  const reviewText = (t: wb.ScreenTrigger): string => {
    if (t.review === 'plan' && t.plan) {
      const shares = t.plan.shares != null ? `建议 ${t.plan.shares} 股`
        : t.plan.sizing_status === 'no_equity' ? '缺资金快照，无法计算股数' : '无建议股数'
      return `入场 ${t.plan.entry_low}–${t.plan.entry_high} · 止损 ${t.plan.stop} · T1 ${t.plan.target1} · ${shares}`
    }
    if (t.review === 'invalid') return `作废 ${t.invalid_reasons.join('、')}`
    return REVIEW_FALLBACK[t.review] ?? t.review
  }

  async function load() {
    const token = ++generation
    loading = true
    try {
      const next = await wb.getToday()
      if (token !== generation) return
      view = next
      error = ''
    } catch (e) {
      if (token === generation) { error = wb.describeError(e); view = null }
    } finally {
      if (token === generation) loading = false
    }
  }

  async function loadTable() {
    const token = ++tableGeneration
    tableLoading = true
    try {
      const next = await wb.getPlanTable(session || undefined, includeExtended)
      if (token !== tableGeneration) return
      table = next
      tableError = ''
    } catch (e) {
      if (token === tableGeneration) { tableError = wb.describeError(e); table = null }
    } finally {
      if (token === tableGeneration) tableLoading = false
    }
  }

  async function recheck(row: wb.PlanTableRow) {
    if (!row.chain || rechecks[row.row_id]?.busy) return
    const { signal_date } = row.chain
    const since = Date.now()
    rechecks[row.row_id] = { busy: true, text: '复查中…', since }
    try {
      const started = await api.recheckChain(row.ticker, signal_date)
      const done = await api.pollRun(started.run_id, { timeoutMs: tl.RECHECK_POLL_MS })
      rechecks[row.row_id] = { busy: false, text: tl.recheckDoneText(done) }
      if (done.status === 'done') await loadTable()
    } catch (e) {
      // 轮询超时不是失败：后台仍在复查，按钮保持不可点，提示稍后刷新（再点会 409 review_in_progress）。
      rechecks[row.row_id] = tl.isRunTimeout(e) ? { busy: true, text: tl.RECHECK_PENDING_TEXT, since }
        : { busy: false, text: e instanceof wb.WorkbenchError && e.status === 409
            ? `不能重新复查：${tl.recheckConflictText(e.message)}` : wb.describeError(e) }
    }
  }

  // 备注（spec §6.5）：作用于一条链，或一份非链计划；写成功后刷新计划表。
  async function addNote(row: wb.PlanTableRow, body: string, requestId: string) {
    const target = row.chain
      ? { ticker: row.ticker, chain_signal_date: row.chain.signal_date }
      : { plan_id: row.plan?.plan_id ?? row.baseline?.plan_id }
    if (!row.chain && !target.plan_id) throw new Error('这一行没有可挂备注的计划')
    await wb.postPlanNote({ request_id: requestId, body, ...target } as wb.PlanNoteBody)
    await loadTable()
  }

  async function runNow() {
    if (running) return
    running = true
    try {
      const run = await wb.runWorkbench()
      notify.success(`已提交批处理（${run.run_id}），完成后刷新本页查看。`)
    } catch (e) {
      notify.error(wb.describeError(e))
    } finally {
      running = false
    }
  }

  async function repush() {
    if (pushing) return
    pushing = true
    try {
      const out = await wb.pushToday()
      if (out.sent) notify.success(`已推送 ${out.messages} 条`)
      else notify.info(`未推送：${out.reason ?? '未知原因'}`)
    } catch (e) {
      notify.error(`推送失败：${wb.describeError(e)}`)
    } finally {
      pushing = false
    }
  }

  // No stored value: tell a missing key from a failed fetch (status recorded in the snapshot inputs).
  const fredMissing = (m: wb.MarketContext) => {
    const status = Object.values(m.inputs?.macro_status ?? {})
    if (status.length && status.every(s => s === 'not_configured')) return 'FRED 未配置'
    if (status.includes('error')) return 'FRED 采集失败（见运行报告）'
    return 'FRED 无数据'
  }
  const fredText = (m: wb.MarketContext) =>
    Object.values(m.macro ?? {}).every(v => v == null)
      ? fredMissing(m)
      : Object.entries(m.macro ?? {}).map(([series, view]) =>
          view == null ? `${series} 无数据` : `${series} ${view.value}（20 期 ${view.change == null ? '无变化' : (Number(view.change) >= 0 ? '+' : '') + view.change}）`).join(' · ')

  // 其他触发：一个触发一行（原来是一行文字，用 · 连起来）
  const otherColumns: Column<wb.Unit>[] = [
    { key: 'unit', header: '触发', value: u => `${u.ticker} · ${u.signal_date}` },
    { key: 'action', header: '建议', value: u => `建议${wb.ACTION_LABELS[u.proposed_action] ?? u.proposed_action}` },
    { key: 'labels', header: '标签', value: u => (u.labels ?? []).map(l => wb.UNIT_LABELS[l] ?? l).join('、') || '无标签' },
    { key: 'rules', header: '规则', value: u => (u.rule_ids ?? []).map(wb.ruleLabel).join('、') || '无规则' },
    { key: 'brief', header: '简报', value: u => (u.brief && u.brief.status !== 'error' ? (u.brief.output?.summary ?? '') : '') || '无简报' },
  ]

  onMount(() => {
    void load()
    void loadTable()
  })
</script>

<div class="grid gap-6">
  <PageHeader level={1} title="今日">
    {#snippet meta()}
      {#if table || view}<span>最近收盘交易日 {table?.session ?? view?.session} · 信号仅为研究候选，不会自动下单</span>{/if}
    {/snippet}
    {#snippet actions()}
      <Button busy={running} onclick={runNow}>立即运行批处理</Button>
      <Button busy={pushing} onclick={repush}>重新推送今日计划</Button>
    {/snippet}
  </PageHeader>

  <!-- v2.6 §7.5：运行一览在最顶部（筛选、市场环境之前）；自己取数，不依赖下面的批处理视图 -->
  <RunOverview />

  {#if error}
    <DataBlock state="error" message={error} onRetry={() => void load()} />
  {:else if loading && !view}
    <DataBlock state="loading" skeleton="row" />
  {/if}

  {#if view}
    <PortfolioCheck check={view.portfolio_check} />
    <div class="grid gap-4 md:grid-cols-2">
      <Card.Root size="sm" role="region" aria-label="筛选" class="gap-2 px-3 text-sm">
        <div class="flex flex-wrap items-center gap-2">
          <h2 class="text-base font-medium">筛选</h2>
          {#if view.screen}<CalcVersion version={view.screen.calc_version} />{/if}
        </div>
        {#if view.screen}
          {@const sc = view.screen}
          {#if sc.status === 'error'}
            <DataBlock state="error" message={`筛选未运行：${sc.error}`} />
            <p class="text-xs text-muted-foreground">沿用成员 {sc.members} 只</p>
          {:else}
            {@const counts = sc.counts ?? {}}
            {@const picked = sc.selected ?? []}
            <!-- Split today's picks, not the groups that passed: setups beyond the cut are not picked (same as the push). -->
            {@const triggerPicked = (sc.triggers ?? []).filter((t) => picked.includes(t.ticker)).length}
            <p>范围 {counts.universe ?? 0} · 通过过滤 {(counts.trigger ?? 0) + (counts.setup ?? 0)}
               · 今日入选 {picked.length}（触发 {triggerPicked} / 形态 {picked.length - triggerPicked}）
               · 成员 {sc.members}/{sc.cap} · 移出 {(sc.removed ?? []).length}</p>
            {#if sc.universe?.stale}<p class="text-xs text-muted-foreground">提示 universe_stale（范围文件超过 120 天）</p>{/if}
            {#if (sc.flags ?? []).length}<p class="text-xs text-muted-foreground">提示 {(sc.flags ?? []).join('、')}</p>{/if}
            <h3 class="font-medium">今日触发 → 复核</h3>
            {#if (sc.triggers ?? []).length}
              <ul class="grid gap-1">
                {#each sc.triggers ?? [] as t (t.ticker)}
                  <li>{t.ticker} · {(t.rule_ids ?? []).join('、')} · RS {t.rs} · {reviewText(t)}</li>
                {/each}
              </ul>
            {:else}
              <DataBlock state="empty" message="今日无触发" />
            {/if}
            <h3 class="font-medium">形态候选（等待触发）</h3>
            {@const setups = screenExpanded && screenFull ? (screenFull.setups ?? []) : (sc.setups ?? [])}
            {#if setups.length}
              <ul class="grid gap-1">
                {#each setups as s (s.ticker)}
                  <li class="flex flex-wrap items-center gap-x-1">{s.ticker} · RS {s.rs} · ATR <MetricValue value={s.atr_pct} percent /></li>
                {/each}
              </ul>
            {:else}
              <DataBlock state="empty" message="今日无形态候选" />
            {/if}
            {#if !screenExpanded && (sc.setups_more ?? 0) > 0}
              <p class="text-xs text-muted-foreground">另 {sc.setups_more} 只 <Button variant="link" size="sm" class="text-xs" onclick={expandScreen}>展开全部</Button></p>
            {/if}
            {#if screenError}<Alert variant="danger"><AlertDescription>展开失败：{screenError}</AlertDescription></Alert>{/if}
          {/if}
        {:else}
          <DataBlock state="not_computed" message="今日无筛选记录" />
        {/if}
      </Card.Root>

      <Card.Root size="sm" role="region" aria-label="市场环境" class="gap-2 px-3 text-sm">
        <div class="flex flex-wrap items-center gap-2">
          <h2 class="text-base font-medium">市场环境</h2>
          {#if view.market}
            <span class="rounded-sm border border-border bg-muted px-1.5 text-xs">{wb.REGIME_LABELS[view.market.label] ?? view.market.label}</span>
          {/if}
        </div>
        {#if view.market}
          {@const m = view.market}
          <p>站上 MA50 <MetricValue value={m.breadth?.pct_50} percent digits={1} /> · MA200 <MetricValue value={m.breadth?.pct_200} percent digits={1} /> · 新高 {m.breadth?.new_highs ?? 0} / 新低 {m.breadth?.new_lows ?? 0}</p>
          {#if m.sectors && Object.keys(m.sectors).length}
            <p class="text-xs text-muted-foreground">{Object.entries(m.sectors).map(([etf, v]) =>
              v == null ? `${etf} 数据不足` : `${etf} rs20 ${wb.pct(v.rs20)} ${wb.SECTOR_STATE_LABELS[v.state] ?? '数据不足'}`).join(' · ')}</p>
          {/if}
          <p class="text-xs text-muted-foreground">{fredText(m)}</p>
        {:else}
          <DataBlock state="not_computed" message="市场环境未生成" />
        {/if}
      </Card.Root>
    </div>
  {/if}

  <!-- v2.9 §10.1：计划表（入场期 / 持仓中 / 已结束）；自己取数、可选日期、扩展池开关 -->
  <Section level={2} title="计划表">
    {#snippet actions()}
      <label class="flex items-center gap-1 text-xs">交易日
        <Input type="date" aria-label="交易日" class="h-7 w-auto text-xs" value={session}
               onchange={(e: Event) => { session = (e.currentTarget as HTMLInputElement).value; void loadTable() }} />
      </label>
      <label class="flex items-center gap-1 text-xs">
        <input type="checkbox" class="size-4 accent-primary" checked={includeExtended}
               onchange={e => { includeExtended = (e.currentTarget as HTMLInputElement).checked; void loadTable() }} />
        显示扩展池（只影子跟踪）
      </label>
    {/snippet}
    {#if tableError}
      <DataBlock state="error" message={tableError} onRetry={() => void loadTable()} />
    {:else if tableLoading && !table}
      <DataBlock state="loading" skeleton="table" />
    {/if}
    {#if table}
      {#each Object.entries(table.errors ?? {}) as [name, reason] (name)}
        <Alert variant="warn" role="status"><AlertDescription>{name} 暂不可用：{reason}</AlertDescription></Alert>
      {/each}
      <PlanTable groups={table.groups} {rechecks} onRecheck={recheck} onNote={addNote} />
    {/if}
  </Section>

  {#if table?.other_units?.length}
    <Section level={2} title="其他触发">
      <DataTable columns={otherColumns} rows={table.other_units} rowKey={u => `${u.ticker}|${u.signal_date}`} sticky={false} />
    </Section>
  {/if}
</div>
