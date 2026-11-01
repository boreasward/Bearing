<script lang="ts">
  import type { AccountPosition } from '$lib/accounts'
  import { decimalText } from '$lib/accounts'
  import { INSTRUCTION_TEXT } from '$lib/workbench'
  import { DataTable } from '$lib/ui/data-table/index'
  import type { Column } from '$lib/ui/data-table/types'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'

  let { positions, full = false, valuationSession, valuationDetail }: {
    positions: AccountPosition[]; full?: boolean; valuationSession?: string | null; valuationDetail?: Record<string, unknown> | null
  } = $props()
  const withUnit = (value: string | number | null, unit: string) => value == null ? '—' : `${value}${unit}`
  const noQuote = (position: AccountPosition) => position.last_close == null || position.last_close === '' || !Number.isFinite(Number(position.last_close)) || !(Number(position.last_close) > 0)
  function quoteReason(position: AccountPosition): string {
    const missing = Array.isArray(valuationDetail?.bars_missing) ? valuationDetail.bars_missing : []
    const stale = valuationDetail?.stale && typeof valuationDetail.stale === 'object' ? valuationDetail.stale as Record<string, unknown> : {}
    return [missing.includes(position.ticker) ? '缺当日收盘' : '', stale[position.ticker] ? `沿用旧收盘 ${stale[position.ticker]}` : '', noQuote(position) ? '无可用收盘价' : ''].filter(Boolean).join('；')
  }
  const warnings = $derived(positions.flatMap(position => {
    const reason = quoteReason(position)
    return reason ? [`${valuationSession ?? '日期未提供'} · ${position.ticker} · ${reason}`] : []
  }))
  const columns = $derived<Column<AccountPosition>[]>([
    { key: 'ticker', header: '标的', cell: ticker }, { key: 'quantity', header: '股数', align: 'num' },
    { key: 'avg_cost', header: '成本', align: 'num' }, { key: 'last_close', header: '现价', align: 'num', value: p => noQuote(p) ? '无可用收盘价' : decimalText(p.last_close) },
    { key: 'market_value', header: '市值', align: 'num', value: p => noQuote(p) ? '缺价格，未计算' : decimalText(p.market_value) },
    { key: 'unrealized_pnl', header: '浮盈', align: 'num', value: p => noQuote(p) ? '缺价格，未计算' : decimalText(p.unrealized_pnl) },
    { key: 'r_now', header: 'R', align: 'num', value: p => noQuote(p) ? '缺价格，未计算' : withUnit(p.r_now, 'R') },
    ...(full ? [{ key: 'stop', header: '止损', align: 'num' as const, value: (p: AccountPosition) => `${decimalText(p.stop)}${p.stop_mode === 'trailing' ? '（移动）' : ''}` },
      { key: 'target1', header: 'T1', align: 'num' as const, value: (p: AccountPosition) => decimalText(p.target1) }] : []),
    { key: 'to_stop_pct', header: '距止损', align: 'num', value: p => noQuote(p) ? '缺价格，未计算' : withUnit(p.to_stop_pct, '%') },
    { key: 'to_t1_pct', header: '距 T1', align: 'num', value: p => noQuote(p) ? '缺价格，未计算' : withUnit(p.to_t1_pct, '%') },
    { key: 'held', header: '持有 / 最长', align: 'num', value: p => `${p.held_sessions ?? '—'} / ${p.max_hold_sessions ?? '—'}` },
    { key: 'plan', header: '所属计划链', cell: plan },
    { key: 'valuation', header: '估值说明', value: p => quoteReason(p) || '无缺口标记' },
    ...(full ? [{ key: 'instruction', header: '下一步', value: (p: AccountPosition) => `${(INSTRUCTION_TEXT as Record<string, string>)[p.instruction] ?? p.instruction}${p.reason ? `：${p.reason}` : ''}` }] : []),
  ])
</script>

{#snippet ticker(p: AccountPosition)}<a class="text-primary underline" href={`#instrument/${p.ticker}`}>{p.ticker}</a>{/snippet}
{#snippet plan(p: AccountPosition)}
  {#if p.plan}<span class="grid gap-1"><a class="text-primary underline" href={`#plan/${p.plan.plan_id}`}>{p.plan.label}</a><span class="text-xs text-muted-foreground">{p.plan.chain_signal_date ?? '—'} · 第 {p.plan.version_no ?? '—'} 版（冻结）</span></span>
  {:else}<span class="text-muted-foreground">无计划持仓</span>{/if}
{/snippet}
<DataBlock state={positions.length ? warnings.length ? 'degraded' : 'ready' : 'empty'} message={warnings.length ? warnings.join('；') : '无持仓'}>
  <DataTable {columns} rows={positions} rowKey={p => p.ticker} cards="narrow" ariaLabel="账户持仓" />
</DataBlock>
