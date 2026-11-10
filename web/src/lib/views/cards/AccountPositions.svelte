<script lang="ts">
  import DataTable from '$lib/ui/data-table/DataTable.svelte'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import { Button } from '$lib/ui/button/index'
  import { record, records, num, text } from '../data'
  import { bringToChat } from '../context'
  let { data }: { data: unknown } = $props()
  const row = $derived(record(data))
  const ledger = $derived(record(row.ledger))
  const positions = $derived(records(ledger.positions))
</script>
{#snippet amount(position: Record<string, unknown>)}<MetricValue value={num(position.unrealized_pnl)} currency tone="updown" />{/snippet}
{#snippet ticker(position: Record<string, unknown>)}<span>{text(position.ticker)}</span> <Button size="sm" variant="ghost" onclick={() => bringToChat({ kind: 'ticker', ticker: String(position.ticker), id: null })}>带进对话</Button>{/snippet}
<DataTable ariaLabel="持仓" columns={[
  { key:'ticker', header:'股票', cell:ticker },
  { key:'quantity', header:'股数', align:'num', value:p => text(p.quantity) },
  { key:'avg_cost', header:'成本', align:'num', value:p => text(p.avg_cost) },
  { key:'unrealized_pnl', header:'浮动盈亏', align:'num', cell:amount }
]} rows={positions} rowKey={p => String(p.ticker)} />
{#if positions.length === 0}<p class="text-sm text-muted-foreground">没有持仓记录</p>{/if}
