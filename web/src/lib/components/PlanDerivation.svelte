<script lang="ts">
  import { onMount } from 'svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import { DataTable } from '$lib/ui/data-table/index'
  import type { Column } from '$lib/ui/data-table/types'
  import * as api from '../api'
  import ConceptTerm from './ConceptTerm.svelte'
  import { ATTACH, derivationStepConcept, ruleConcept } from '../knowledge'
  import { CLAUSE_TEXT, SOURCE_TEXT } from '../derivation'
  import type { Derivation, Step, TriggerClause, TriggerItem, UnitDerivation } from '../derivation'

  // 二者给一个：planId 展开完整四段推导，unit 只展开触发段；挂载时请求一次。
  let { planId, unit }: { planId?: string; unit?: { ticker: string; signal_date: string } } = $props()

  let loading = $state(true)
  let error = $state('')
  let plan = $state<Derivation | null>(null)
  let triggerOnly = $state<UnitDerivation | null>(null)
  const D = ATTACH.derivation

  onMount(() => { void load() })

  async function load() {
    loading = true
    error = ''
    try {
      if (planId) plan = await api.getPlanDerivation(planId)
      else if (unit) triggerOnly = await api.getUnitDerivation(unit.ticker, unit.signal_date)
    } catch (e) {
      error = String((e as Error)?.message ?? e)
    } finally {
      loading = false
    }
  }

  // 数字照后端字符串原样展示：不做任何 Number() / toFixed。
  const text = (value: unknown): string => value == null ? '无' : String(value)
  const operand = (value: unknown): string =>
    Array.isArray(value) ? (value.join(',') || '无') : value == null ? '无' : String(value)
  const pairs = (entries: Record<string, unknown> | undefined): string =>
    Object.entries(entries ?? {}).map(([k, v]) => `${k}=${operand(v)}`).join(' · ') || '无'

  const consistency = $derived(plan?.consistency ?? triggerOnly?.consistency ?? null)
  const candidates = $derived<TriggerItem[]>(plan?.trigger.candidates ?? triggerOnly?.trigger ?? [])
  const triggerNote = $derived(plan?.trigger.note ?? triggerOnly?.note ?? '')
  const steps = $derived(plan?.steps ?? [])
  const trSteps = $derived(steps.filter(s => s.key.startsWith('tr_')))      // TR1..TR14 明细默认折叠
  const mainSteps = $derived(steps.filter(s => !s.key.startsWith('tr_')))

  const clauseClass = (result: string): string =>
    result === 'violated' ? 'text-destructive' : result === 'not_frozen' ? 'text-warn' : 'text-muted-foreground'
  const sourceLabel = (step: Step): string => {
    const refs = step.refs?.length ? ` ${step.refs.join('、')}` : ''
    return `${SOURCE_TEXT[step.source] ?? step.source}${refs}`
  }

  const stepColumns: Column<Step>[] = [
    { key: 'label', header: '项目', cell: stepLabel },
    { key: 'formula', header: '公式', value: s => s.formula },
    { key: 'substitutions', header: '代入', cell: stepSubstitutions },
    { key: 'result', header: '结果', align: 'num', value: s => text(s.result) },
    { key: 'stored', header: '已存', align: 'num', value: s => text(s.stored) },
    { key: 'consistent', header: '一致', cell: stepConsistent },
  ]
  const clauseColumns: Column<TriggerClause>[] = [
    { key: 'text', header: '条件句', value: c => c.text },
    { key: 'operands', header: '冻结取值', cell: clauseOperands },
    { key: 'result', header: '结果', cell: clauseResult },
  ]
  const barColumns: Column<{ id: string | number; price_date: string; open: unknown; high: unknown; low: unknown; close: unknown; revision: unknown }>[] = [
    { key: 'price_date', header: '日期', value: b => b.price_date },
    { key: 'open', header: '开', align: 'num', value: b => text(b.open) },
    { key: 'high', header: '高', align: 'num', value: b => text(b.high) },
    { key: 'low', header: '低', align: 'num', value: b => text(b.low) },
    { key: 'close', header: '收', align: 'num', value: b => text(b.close) },
    { key: 'revision', header: '修订号', align: 'num', value: b => text(b.revision) },
  ]
</script>

{#snippet stepLabel(step: Step)}
  {step.label}（{sourceLabel(step)}）{#if derivationStepConcept(step.key)} <ConceptTerm id={derivationStepConcept(step.key)!} />{/if}
{/snippet}
{#snippet stepSubstitutions(step: Step)}{pairs(step.substitutions)}{/snippet}
{#snippet stepConsistent(step: Step)}
  <span class={step.consistent === false ? 'text-destructive' : 'text-muted-foreground'}>{step.consistent == null ? '不适用' : step.consistent ? '一致' : '不一致'}</span>
{/snippet}
{#snippet clauseOperands(clause: TriggerClause)}{pairs(clause.operands)}{/snippet}
{#snippet clauseResult(clause: TriggerClause)}<span class={clauseClass(clause.result)}>{CLAUSE_TEXT[clause.result]}</span>{/snippet}

<section class="text-sm border-l-2 border-border pl-2 space-y-2" aria-label="计划推导">
  {#if error}
    <DataBlock state="error" message={error} onRetry={() => void load()} />
  {:else if loading}
    <DataBlock state="loading" skeleton="block" />
  {:else}
    {#if consistency}
      <p class="flex flex-wrap items-center gap-2">
        <StatusPill domain="consistency" value={consistency.status} />
        <span class="text-xs"><ConceptTerm id={D.consistency}>推导核对</ConceptTerm></span>
        <span class="text-xs text-muted-foreground">{plan?.derivation_version ?? triggerOnly?.derivation_version}</span>
      </p>
      {#each consistency.mismatches as m (m.key)}
        <p class="text-xs text-destructive">差异 {m.key}：推导 {text(m.result)} ≠ 已存 {text(m.stored)}</p>
      {/each}
      {#each consistency.missing as item (item)}
        <p class="text-xs text-warn">缺失 {item}</p>
      {/each}
    {/if}
    <p class="text-xs text-muted-foreground">{plan?.evidence ?? triggerOnly?.evidence} <ConceptTerm id={D.evidence} /></p>

    <details open class="space-y-1">
      <summary class="cursor-pointer font-medium text-xs">触发</summary>
      {#if triggerNote}<p class="text-xs text-muted-foreground">{triggerNote}</p>{/if}
      {#each candidates as c (c.candidate_id)}
        <div class="space-y-0.5">
          <p class="text-xs"><ConceptTerm id={ruleConcept(c.rule_id)}>{c.rule_id}</ConceptTerm> · v{c.rule_version} · {c.action}{c.note ? ` · ${c.note}` : ''}</p>
          <DataTable columns={clauseColumns} rows={c.clauses} rowKey={(_clause) => c.clauses.indexOf(_clause)} ariaLabel={`触发条件 ${c.rule_id}`} cards="narrow" sticky={false} />
        </div>
      {:else}
        <p class="text-xs text-muted-foreground">无候选</p>
      {/each}
    </details>

    {#if plan}
      <details open class="space-y-1">
        <summary class="cursor-pointer font-medium text-xs">输入快照</summary>
        <p class="text-xs"><ConceptTerm id={D.refPrice}>参考价</ConceptTerm> {text(plan.inputs.ref_price?.value)}（bar {text(plan.inputs.ref_price?.source_bar_id)}）</p>
        <p class="text-xs"><ConceptTerm id={D.earnings}>财报</ConceptTerm> {plan.inputs.earnings ? `${plan.inputs.earnings.status ?? '无'} · ${text(plan.inputs.earnings.earnings_date)} · 取于 ${text(plan.inputs.earnings.fetched_at)}` : text(plan.inputs.earnings_note)}</p>
        <p class="text-xs">{#if plan.inputs.equity_source === 'ledger'}账本权益{:else}<ConceptTerm id={D.snapshot}>资金快照</ConceptTerm>{/if} {plan.inputs.snapshot ? `${text(plan.inputs.snapshot.as_of)} · equity ${text(plan.inputs.snapshot.equity)}` : text(plan.inputs.snapshot_note)}</p>
        {#if plan.inputs.exposure}
          <p class="text-xs"><ConceptTerm id={D.exposure}>敞口</ConceptTerm> 该票 {text(plan.inputs.exposure.ticker_value)} / 总持仓 {text(plan.inputs.exposure.total_value)}（{plan.inputs.exposure.note}）</p>
        {/if}
        {#if plan.inputs.sizing_params}
          <p class="text-xs"><ConceptTerm id={D.sizingParams}>仓位参数</ConceptTerm> risk_pct {text(plan.inputs.sizing_params.risk_pct)} · single_cap {text(plan.inputs.sizing_params.single_cap)} · total_cap {text(plan.inputs.sizing_params.total_cap)}（{plan.inputs.sizing_params.note}）</p>
        {/if}
        {#if plan.inputs.regime}
          <p class="text-xs"><ConceptTerm id={D.regime}>区制</ConceptTerm> {text(plan.inputs.regime.label)}（{text(plan.inputs.regime.calc_version)}）</p>
        {/if}
        {#if plan.inputs.sector}
          <p class="text-xs"><ConceptTerm id={D.sector}>行业</ConceptTerm> {text(plan.inputs.sector.etf)} rs20 {text(plan.inputs.sector.rs20)} · rs60 {text(plan.inputs.sector.rs60)} · {text(plan.inputs.sector.state)}</p>
        {/if}
        {#if plan.inputs.next_earnings}<p class="text-xs">下次财报 {plan.inputs.next_earnings}</p>{/if}
        {#if plan.inputs.pool_tier}<p class="text-xs">池 {plan.inputs.pool_tier}</p>{/if}
        {#if plan.inputs.source}<p class="text-xs">来源 {plan.inputs.source}</p>{/if}
        {#if plan.inputs.screen}
          <p class="text-xs">筛选 run {text(plan.inputs.screen.screen_run_id)} · {text(plan.inputs.screen.screen_group)} · RS {text(plan.inputs.screen.rs)} · 排名 {text(plan.inputs.screen.rank)}</p>
        {/if}
        {#if plan.inputs.agent_run}
          <p class="text-xs">Agent {text(plan.inputs.agent_run.model)} · {text(plan.inputs.agent_run.agent_version)}</p>
        {/if}
        {#if plan.inputs.bars}
          <details class="space-y-1">
            <summary class="cursor-pointer text-xs">日线（{plan.inputs.bars.length} 根，{plan.inputs.bars_note}）</summary>
            <p class="text-xs"><ConceptTerm id={D.bars} /></p>
            <DataTable columns={barColumns} rows={plan.inputs.bars} rowKey={(b) => b.id} ariaLabel="输入日线" cards="narrow" sticky={false} />
          </details>
        {/if}
      </details>

      <details open class="space-y-1">
        <summary class="cursor-pointer font-medium text-xs">逐项计算</summary>
        {#if trSteps.length}
          <details class="space-y-1">
            <summary class="cursor-pointer text-xs">TR 明细（{trSteps.length} 步）</summary>
            <DataTable columns={stepColumns} rows={trSteps} rowKey={(r) => r.key} ariaLabel="TR 明细" cards="narrow" sticky={false} />
          </details>
        {/if}
        {#if mainSteps.length}
          <DataTable columns={stepColumns} rows={mainSteps} rowKey={(r) => r.key} ariaLabel="逐项计算" cards="narrow" sticky={false} />
        {:else}
          <p class="text-xs text-muted-foreground">无可推导项</p>
        {/if}
      </details>

      <details open class="space-y-1">
        <summary class="cursor-pointer font-medium text-xs">校验</summary>
        {#each plan.checks as check (check.key)}
          <p class="text-xs">{check.label}：{check.formula}（{pairs(check.substitutions)}）
            <span class={check.holds ? 'text-muted-foreground' : 'text-destructive'}>{check.holds ? '成立' : '不成立'}</span></p>
        {:else}
          <p class="text-xs text-muted-foreground">无校验项</p>
        {/each}
      </details>

      {#if plan.run}
        <p class="text-xs text-muted-foreground"><span class="font-medium"><ConceptTerm id={D.run}>运行证据：</ConceptTerm></span>
          {#if !plan.run.ready}
            <span>{text(plan.run.note)}</span>
          {:else if plan.run.run_id}
            <span>{plan.run.run_id} · 阶段 {text(plan.run.stage)} · {text(plan.run.status)}{#if plan.run.derivation} · 推导核对 {plan.run.derivation.checked} 份：consistent {plan.run.derivation.consistent} · mismatch {plan.run.derivation.mismatch} · incomplete {plan.run.derivation.incomplete}{/if}</span>
          {:else}
            <span>{text(plan.run.note)}</span>
          {/if}
        </p>
      {/if}
    {/if}
  {/if}
</section>
