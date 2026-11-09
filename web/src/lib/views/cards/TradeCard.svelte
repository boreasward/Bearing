<script lang="ts">
  import CardFrame from './CardFrame.svelte'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import PlanLevels from '$lib/ui/domain/PlanLevels.svelte'
  import { num, record, text } from '../data'
  import type { CallMeta } from '../registry'
  let { data, meta }: { data: unknown; meta: CallMeta } = $props()
  const row = $derived(record(data))
  const trade = $derived(record(row.trade))
  const plan = $derived(record(row.plan))
  const stop = $derived(record(row.stop_order))
</script>
<CardFrame title="成交记录" {data} {meta} contextChip={typeof trade.ticker === 'string' ? { kind: num(trade.plan_id) === null ? 'ticker' : 'plan', ticker: String(trade.ticker), id: num(trade.plan_id) } : null}>
  <p class="text-sm">{text(trade.ticker)} · {trade.side === 'buy' ? '买入' : trade.side === 'sell' ? '卖出' : '没算'} {text(trade.quantity)} 股 @ <MetricValue value={num(trade.price)} currency /></p>
  <p class="text-sm">计划变化：{#if plan.id}<a class="underline" href={`#plans/${plan.id}`}>计划 #{text(plan.id)}</a>{:else}没算{/if}</p>
  <PlanLevels low={num(plan.entry_low)} high={num(plan.entry_high)} stop={num(plan.stop)} target1={num(plan.target1)} />
  <p class="text-sm">止损挂单：{text(stop.quantity)} 股 @ <MetricValue value={num(stop.price)} currency /></p>
  <p class="text-sm">平仓结果：{text(row.outcome)}</p>
</CardFrame>
