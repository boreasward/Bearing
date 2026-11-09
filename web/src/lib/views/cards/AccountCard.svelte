<script lang="ts">
  import CardFrame from './CardFrame.svelte'
  import DataTable from '$lib/ui/data-table/DataTable.svelte'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import { Button } from '$lib/ui/button/index'
  import CashFlowForm from '$lib/home/CashFlowForm.svelte'
  import ReconcileForm from '$lib/home/ReconcileForm.svelte'
  import { invalidateAll } from '$lib/stores'
  import { record, records, num, text } from '../data'
  import { actionCompleted, bringToChat } from '../context'
  import type { CallMeta } from '../registry'
  let { data, meta }: { data: unknown; meta?: CallMeta } = $props()
  const row = $derived(record(data))
  const ledger = $derived(record(row.ledger))
  const positions = $derived(records(ledger.positions))
  const unrealized = $derived(ledger.positions !== undefined && positions.every(p => num(p.unrealized_pnl) !== null) ? positions.reduce((sum, p) => sum + num(p.unrealized_pnl)!, 0) : null)
  let cashOpen = $state(false)
  let reconcileOpen = $state(false)
  function completed(message: string) { invalidateAll(); actionCompleted(message) }
</script>
{#snippet amount(position: Record<string, unknown>)}<MetricValue value={num(position.unrealized_pnl)} currency tone="updown" />{/snippet}
{#snippet ticker(position: Record<string, unknown>)}<span>{text(position.ticker)}</span> <Button size="sm" variant="ghost" onclick={() => bringToChat({ kind: 'ticker', ticker: String(position.ticker), id: null })}>带进对话</Button>{/snippet}
<CardFrame title="账户" {data} {meta}>
  <dl class="grid grid-cols-2 gap-2 text-sm">
    <dt>现金</dt><dd><MetricValue value={num(ledger.cash)} currency /></dd>
    <dt>总资产</dt><dd><MetricValue value={num(ledger.equity)} currency /></dd>
    <dt>浮动盈亏</dt><dd><MetricValue value={unrealized} currency tone="updown" /></dd>
    <dt>已实现盈亏</dt><dd><MetricValue value={num(ledger.realized_pnl)} currency tone="updown" /></dd>
    <dt>对账差额</dt><dd><MetricValue value={num(record(row.reconciliation).difference)} currency /></dd>
  </dl>
  {#if row.amount != null}<p>出入金：<MetricValue value={num(row.amount)} currency /> · {text(row.effective_at)}</p>{/if}
  {#if row.equity != null}<p>对账总资产：<MetricValue value={num(row.equity)} currency /> · {text(row.as_of)}</p>{/if}
  {#if ledger.cash_negative}<p class="text-sm text-warn">现金为负，请核对账本</p>{/if}
  {#if Array.isArray(ledger.notes)}{#each ledger.notes as note, index (index)}<p class="text-sm text-muted-foreground">{text(note)}</p>{/each}{/if}
  <DataTable ariaLabel="持仓"
    columns={[
      { key:'ticker', header:'股票', cell:ticker },
      { key:'quantity', header:'股数', align:'num', value:p => text(p.quantity) },
      { key:'avg_cost', header:'成本', align:'num', value:p => text(p.avg_cost) },
      { key:'unrealized_pnl', header:'浮动盈亏', align:'num', cell:amount }
    ]}
   rows={positions} rowKey={p => String(p.ticker)} />
  {#if positions.length === 0}<p class="text-sm text-muted-foreground">没有持仓记录</p>{/if}
  <p class="text-xs text-muted-foreground">仓位快照来源：{text(record(row.sizing_snapshot).source)} · {text(record(row.sizing_snapshot).as_of)}</p>
  <div class="flex flex-wrap gap-2"><Button size="sm" variant="outline" onclick={() => cashOpen = true}>记出入金</Button><Button size="sm" variant="outline" onclick={() => reconcileOpen = true}>对账</Button></div>
</CardFrame>
<CashFlowForm bind:open={cashOpen} onDone={() => completed('已记录出入金')} />
<ReconcileForm bind:open={reconcileOpen} onDone={() => completed('已记录对账')} />
