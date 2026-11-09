<script lang="ts">
  import CardFrame from './CardFrame.svelte'
  import { record, text, num } from '../data'
  import type { CallMeta } from '../registry'
  let { data, meta }: { data: unknown; meta: CallMeta } = $props()
  const row = $derived(record(data))
</script>
<CardFrame title="记录" {data} meta={{ ...meta, write: meta.write ?? 'written' }} contextChip={typeof record(row.plan).ticker === 'string' ? { kind: 'plan', ticker: String(record(row.plan).ticker), id: num(record(row.plan).id) } : null}>
  <p class="whitespace-pre-wrap text-sm">{text(row.summary ?? row.reasons ?? record(row.review).reasons)}</p>
</CardFrame>
