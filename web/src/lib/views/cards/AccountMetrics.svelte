<script lang="ts">
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import { num, record, text } from '../data'
  let { data }: { data: unknown } = $props()
  const row = $derived(record(data))
  const ledger = $derived(record(row.ledger))
  const positions = $derived(Array.isArray(ledger.positions) ? ledger.positions as Record<string, unknown>[] : [])
  const unrealized = $derived(ledger.positions !== undefined && positions.every(p => num(p.unrealized_pnl) !== null) ? positions.reduce((sum, p) => sum + num(p.unrealized_pnl)!, 0) : null)
</script>
<dl class="grid grid-cols-2 gap-2 text-sm">
  <dt>现金</dt><dd><MetricValue value={num(ledger.cash)} currency /></dd>
  <dt>总资产</dt><dd><MetricValue value={num(ledger.equity)} currency /></dd>
  <dt>浮动盈亏</dt><dd><MetricValue value={unrealized} currency tone="updown" /></dd>
  <dt>已实现盈亏</dt><dd><MetricValue value={num(ledger.realized_pnl)} currency tone="updown" /></dd>
  <dt>对账差额</dt><dd><MetricValue value={num(record(row.reconciliation).difference)} currency /></dd>
</dl>
{#if ledger.cash_negative}<p class="text-sm text-warn">现金为负，请核对账本</p>{/if}
{#if Array.isArray(ledger.notes)}{#each ledger.notes as note, index (index)}<p class="text-sm text-muted-foreground">{text(note)}</p>{/each}{/if}
<p class="text-xs text-muted-foreground">仓位快照来源：{text(record(row.sizing_snapshot).source)} · {text(record(row.sizing_snapshot).as_of)}</p>
