<script lang="ts">
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import { Button } from '$lib/ui/button/index'
  import CardFrame from './CardFrame.svelte'
  import { record, records, text, num } from '../data'
  import { bringToChat } from '../context'
  import type { CallMeta } from '../registry'
  let { data, meta }: { data: unknown; meta: CallMeta } = $props()
  const rows = $derived(Array.isArray(data) ? records(data) : Array.isArray(record(data).research) ? records(record(data).research) : [record(record(data).research ?? data)])
</script>
<CardFrame title="研究快照" {data} {meta} contextChip={rows.length === 1 && typeof rows[0].ticker === 'string' && num(rows[0].id) !== null ? { kind: 'research', ticker: String(rows[0].ticker), id: num(rows[0].id) } : null}>
  <DataBlock state={rows.length ? 'ready' : 'empty'} message="没有符合条件的研究">
    {#each rows as row, index (index)}
      <section class="grid gap-2">
        <h4 class="flex flex-wrap items-center gap-2 font-serif font-semibold">{text(row.ticker)} {#if row.status}<StatusPill domain="research" value={String(row.status)} />{/if}</h4>
        <p>{text(row.thesis)}</p><p class="text-sm">条件：{text(row.condition_text)}</p><p class="text-xs text-muted-foreground">有效至 {text(row.valid_until)}</p>
        {#if row.id != null}<div class="flex flex-wrap gap-2"><a class="text-sm underline" href={`#research/${row.id}`}>打开研究</a>{#if rows.length > 1}<Button size="sm" variant="ghost" onclick={() => bringToChat({ kind: 'research', ticker: String(row.ticker), id: num(row.id) })}>就这个继续问</Button>{/if}</div>{/if}
      </section>
    {/each}
  </DataBlock>
</CardFrame>
