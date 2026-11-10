<script lang="ts">
  import CardFrame from './CardFrame.svelte'
  import AccountMetrics from './AccountMetrics.svelte'
  import AccountPositions from './AccountPositions.svelte'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import { Button } from '$lib/ui/button/index'
  import CashFlowForm from '$lib/home/CashFlowForm.svelte'
  import ReconcileForm from '$lib/home/ReconcileForm.svelte'
  import { invalidateAll } from '$lib/stores'
  import { record, num, text } from '../data'
  import { actionCompleted } from '../context'
  import type { CallMeta } from '../registry'
  let { data, meta }: { data: unknown; meta?: CallMeta } = $props()
  const row = $derived(record(data))
  let cashOpen = $state(false)
  let reconcileOpen = $state(false)
  function completed(message: string) { invalidateAll(); actionCompleted(message) }
</script>
<CardFrame title="账户" {data} {meta}>
  <AccountMetrics {data} />
  {#if row.amount != null}<p>出入金：<MetricValue value={num(row.amount)} currency /> · {text(row.effective_at)}</p>{/if}
  {#if row.equity != null}<p>对账总资产：<MetricValue value={num(row.equity)} currency /> · {text(row.as_of)}</p>{/if}
  <AccountPositions {data} />
  <div class="flex flex-wrap gap-2"><Button size="sm" variant="outline" onclick={() => cashOpen = true}>记出入金</Button><Button size="sm" variant="outline" onclick={() => reconcileOpen = true}>对账</Button></div>
</CardFrame>
<CashFlowForm bind:open={cashOpen} onDone={() => completed('已记录出入金')} />
<ReconcileForm bind:open={reconcileOpen} onDone={() => completed('已记录对账')} />
