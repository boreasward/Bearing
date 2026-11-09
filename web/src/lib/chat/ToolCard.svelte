<script lang="ts">
  import { onMount } from 'svelte'
  import { get } from '$lib/api'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import * as Card from '$lib/ui/card/index'
  import { viewFor } from '$lib/views/registry'
  import SourceLine from '$lib/views/cards/SourceLine.svelte'
  import { record } from '$lib/views/data'
  import type { CallData } from '$lib/stores/types'
  import type { Block } from './types'
  let { block, chatId, agentSession, lazy = false }: {
    block: Extract<Block, { kind: 'tool' }>; chatId: string; agentSession: string | null; lazy?: boolean
  } = $props()
  let root: HTMLDivElement
  let visible = $state(false)
  let fetching = $state(false)
  let requestKey = ''
  const entry = $derived(viewFor(block.tool))
  const call = $derived(block.data as CallData | undefined)
  const noResult = $derived(call !== undefined && call.result === null)
  const data = $derived(call?.error ? { error: call.error } : call?.result)
  const session = $derived(typeof record(call?.result).session === 'string' ? String(record(call?.result).session) : null)

  onMount(() => {
    if (!lazy || typeof IntersectionObserver === 'undefined') { visible = true; return }
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { visible = true; observer.disconnect() }
    })
    observer.observe(root)
    return () => observer.disconnect()
  })
  $effect(() => {
    if (!visible || block.state !== 'ready' || !block.ref || call !== undefined || fetching) return
    const key = `${chatId}/${agentSession}/${block.ref}`
    if (key === requestKey) return
    requestKey = key
    if (!agentSession) { block.state = 'untraceable'; return }
    fetching = true
    const params = new URLSearchParams({ agent_session: agentSession, ref: block.ref })
    // Keep call refs literal in the request as in the API contract.
    const path = `/api/chats/${encodeURIComponent(chatId)}/calls?${params.toString().replace(/%3A/g, ':')}`
    get<CallData>(path, { errorDetails: true }).then(result => {
      block.data = result
      block.tool = result.tool ?? block.tool
    }).catch(() => { block.state = 'error'; block.error = '调用结果加载失败，请重试' }).finally(() => { fetching = false })
  })
  function retry() { requestKey = ''; block.state = 'ready'; block.error = undefined }
</script>
<div bind:this={root} data-call-ref={block.ref}>
  {#if block.state === 'ready' && call && !noResult && !call.truncated}
    <entry.component {data} meta={{ tool: block.tool, ref: block.ref, session, calledAt: call.called_at }} />
  {:else}
    <Card.Root size="sm"><Card.Header>
      <Card.Title>{entry.title(undefined)}{block.state === 'calling' ? ' · 调用中' : ''}</Card.Title>
      {#if block.ref || block.state === 'calling'}<SourceLine meta={{ tool: block.tool, ref: block.ref, session, calledAt: call?.called_at ?? null }} />
      {:else}<p class="font-mono text-xs text-muted-foreground">{block.tool}</p>{/if}
    </Card.Header><Card.Content>
      <DataBlock state={block.state === 'untraceable' || noResult || call?.truncated ? 'degraded' : block.state === 'error' ? 'error' : 'loading'}
        message={noResult ? '这次调用早于 v3.2.1，没有保留结果' : call?.truncated ? '调用结果过大，只保留了截断记录' : block.state === 'untraceable' ? '调用已完成，结果不可追溯' : block.error}
        onRetry={block.state === 'error' && block.ref && agentSession ? retry : undefined} />
    </Card.Content></Card.Root>
  {/if}
</div>
