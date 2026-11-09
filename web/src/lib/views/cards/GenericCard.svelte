<script lang="ts">
  import DataTable from '$lib/ui/data-table/DataTable.svelte'
  import { Button } from '$lib/ui/button/index'
  import CardFrame from './CardFrame.svelte'
  import { FIELD_LABELS, TOOL_LABELS } from '../labels'
  import { record, records, text, type Row } from '../data'
  import type { CallMeta, ToolName } from '../registry'
  let { data, meta }: { data: unknown; meta: CallMeta } = $props()
  let expanded = $state(false)
  const flat = $derived(data !== null && !Array.isArray(data) && typeof data === 'object' && Object.values(record(data)).every(v => v === null || typeof v !== 'object'))
  const rows = $derived(records(data).map((row, index) => ({ ...row, __row: index })))
  const keys = $derived([...new Set(records(data).flatMap(row => Object.keys(row)))])
  const lines = $derived((JSON.stringify(data, null, 2) ?? '没算').split('\n'))
</script>
<CardFrame title={TOOL_LABELS[meta.tool as ToolName] ?? meta.tool} {data} {meta}>
  {#if flat}
    <dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
      {#each Object.entries(record(data)) as [key, value] (key)}
        <dt class={FIELD_LABELS[key] ? 'text-muted-foreground' : 'font-mono text-muted-foreground'}>{FIELD_LABELS[key] ?? key}</dt><dd class="break-all">{text(value)}</dd>
      {/each}
    </dl>
  {:else if Array.isArray(data) && data.every(v => v !== null && typeof v === 'object' && !Array.isArray(v))}
    <DataTable columns={keys.slice(0, 8).map(key => ({ key, header: FIELD_LABELS[key] ?? key, value: (row: Row) => text(row[key]) }))} {rows} rowKey={row => row.__row} />
    {#if keys.length > 8}<p class="text-xs text-muted-foreground">还有 {keys.length - 8} 列</p>{/if}
  {:else}
    <pre class="overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs">{(expanded ? lines : lines.slice(0, 40)).join('\n')}</pre>
    {#if lines.length > 40}<Button size="sm" variant="ghost" aria-expanded={expanded} onclick={() => expanded = !expanded}>{expanded ? '收起' : '展开'}</Button>{/if}
  {/if}
</CardFrame>
