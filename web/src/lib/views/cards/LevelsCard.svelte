<script lang="ts">
  import PlanLevels from '$lib/ui/domain/PlanLevels.svelte'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import CardFrame from './CardFrame.svelte'
  import { record, num, text } from '../data'
  import type { CallMeta } from '../registry'
  let { data, meta }: { data: unknown; meta: CallMeta } = $props()
  const row = $derived(record(data))
  const levels = $derived(record(row.levels))
</script>
<CardFrame title="计划价位" {data} {meta}>
  <PlanLevels low={num(levels.entry_low)} high={num(levels.entry_high)} stop={num(levels.stop)} target1={num(levels.target1)} shares={num(record(row.sizing).shares)} />
  <p class="text-sm">ATR <MetricValue value={num(row.atr)} /> · 口径 {text(row.levels_source ?? 'host')} · 参考价 <MetricValue value={num(row.ref_price)} currency /></p>
  <p class="text-xs text-muted-foreground">{text(row.session)} · 有效至 {text(row.valid_until)}</p>
</CardFrame>
