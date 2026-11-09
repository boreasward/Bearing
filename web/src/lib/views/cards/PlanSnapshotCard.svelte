<script lang="ts">
  import PlanLevels from '$lib/ui/domain/PlanLevels.svelte'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import { Button } from '$lib/ui/button/index'
  import TradeForm from '$lib/home/TradeForm.svelte'
  import { invalidateAll } from '$lib/stores'
  import CardFrame from './CardFrame.svelte'
  import { num, record, records, text, type Row } from '../data'
  import { actionCompleted, bringToChat } from '../context'
  import type { CallMeta } from '../registry'
  let { data, meta }: { data: unknown; meta: CallMeta } = $props()
  const rows = $derived(Array.isArray(data) ? records(data) : Array.isArray(record(data).plans) ? records(record(data).plans) : [record(record(data).plan ?? data)])
  let target = $state<Row | null>(null)
  let tradeOpen = $state(false)
</script>
<CardFrame title="计划快照" {data} {meta} contextChip={rows.length === 1 && typeof rows[0].ticker === 'string' && num(rows[0].id ?? rows[0].plan_id) !== null ? { kind: 'plan', ticker: String(rows[0].ticker), id: num(rows[0].id ?? rows[0].plan_id) } : null}>
  <DataBlock state={rows.length ? 'ready' : 'empty'} message="没有符合条件的计划">
    {#each rows as row, index (index)}
      <section class="grid gap-2">
        <h4 class="flex flex-wrap items-center gap-2 font-serif font-semibold">{text(row.ticker)} {#if row.status || meta.tool === 'get_positions'}<StatusPill domain="plan" value={String(row.status ?? 'open')} />{/if}</h4>
        <PlanLevels low={num(row.entry_low)} high={num(row.entry_high)} stop={num(row.stop)} target1={num(row.target1)} shares={num(row.shares)} />
        {#if row.quantity != null}<p class="text-sm">持仓 {text(row.quantity)} 股 · 成本 <MetricValue value={num(row.avg_cost)} currency /></p>{/if}
        <p class="text-xs text-muted-foreground">有效期 {text(row.valid_from)} 至 {text(row.valid_until)}</p>
        <p class="text-sm">{text(row.thesis)}</p>
        {#if num(row.id ?? row.plan_id) !== null}
          <div class="flex flex-wrap gap-2">
            <a class="text-sm underline" href={`#plans/${row.id ?? row.plan_id}`}>打开计划</a>
            <Button size="sm" variant="outline" onclick={() => { target = row; tradeOpen = true }}>记成交</Button>
            {#if rows.length > 1}<Button size="sm" variant="ghost" onclick={() => bringToChat({ kind: 'plan', ticker: String(row.ticker), id: num(row.id ?? row.plan_id) })}>就这个继续问</Button>{/if}
          </div>
        {/if}
      </section>
    {/each}
  </DataBlock>
</CardFrame>
<TradeForm bind:open={tradeOpen} ticker={String(target?.ticker ?? '')} planId={num(target?.id ?? target?.plan_id)} shares={num(target?.shares ?? target?.quantity)} onDone={() => { invalidateAll(); actionCompleted('已记录成交') }} />
