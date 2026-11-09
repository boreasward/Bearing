<script lang="ts">
  import type { Snippet } from 'svelte'
  import * as Card from '$lib/ui/card/index'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import { Button } from '$lib/ui/button/index'
  import { bringToChat, type ContextChip } from '../context'
  import SourceLine from './SourceLine.svelte'
  import { record, toolError } from '../data'
  import type { CallMeta } from '../registry'
  let { title, data, meta, calcVersion, children, badges, contextChip, emptyMessage = '这次查询没有返回记录' }: {
    title: string; data: unknown; meta?: CallMeta; calcVersion?: string | null
    children: Snippet; badges?: Snippet; contextChip?: ContextChip | null; emptyMessage?: string
  } = $props()
  const error = $derived(toolError(data))
  const preview = $derived(record(data).write === false)
  const empty = $derived(data == null || (Array.isArray(data) && data.length === 0))
  const chip = $derived(contextChip ?? (typeof record(data).ticker === 'string' ? { kind: 'ticker' as const, ticker: String(record(data).ticker), id: null } : null))
</script>
<Card.Root size="sm">
  <Card.Header>
    <Card.Title class="flex flex-wrap items-center gap-2">{title}
      {#if preview}<StatusPill domain="write" value="preview" />{:else if meta?.write}<StatusPill domain="write" value={meta.write} />{/if}
      {#if !error && !preview}{@render badges?.()}{/if}
    </Card.Title>
    {#if chip && !error && !preview}
      <Card.Action><Button size="sm" variant="ghost" onclick={() => chip && bringToChat(chip)}>就这个继续问</Button></Card.Action>
    {/if}
    {#if meta}<SourceLine {meta} {calcVersion} />{:else if calcVersion}<Card.Description>口径 {calcVersion}</Card.Description>{/if}
  </Card.Header>
  <Card.Content class="grid gap-3">
    <DataBlock state={error ? 'error' : empty ? 'empty' : 'ready'} message={error ?? emptyMessage}>
      {#if preview}<p class="text-sm">{String(record(data).preview ?? '请确认后写入')}</p>{:else}{@render children()}{/if}
    </DataBlock>
  </Card.Content>
</Card.Root>
