<script lang="ts">
  import { onDestroy } from 'svelte'
  import RefLink from '$lib/ui/domain/RefLink.svelte'
  import ToolCard from './ToolCard.svelte'
  import type { Turn } from './types'
  let { turn, chatId, history = false }: { turn: Turn; chatId: string; history?: boolean } = $props()
  let root: HTMLDivElement
  let highlighted = $state<string | null>(null)
  let timer: ReturnType<typeof setTimeout> | undefined
  function scrollToCall(ref: string) {
    const card = [...root.querySelectorAll<HTMLElement>('[data-call-ref]')].find(node => node.dataset.callRef === ref)
    card?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
    highlighted = ref
    clearTimeout(timer)
    timer = setTimeout(() => { highlighted = null }, 1500)
  }
  onDestroy(() => clearTimeout(timer))
</script>
<div bind:this={root} class="grid gap-3 text-[13px] leading-[1.55]" aria-label="助手回合">
  {#if turn.finished === 'running'}<span class="text-xs text-muted-foreground">回答中…</span>{/if}
  {#each turn.blocks as block}
    {#if block.kind === 'tool'}
      <div class={highlighted === block.ref ? 'rounded-xl ring-2 ring-ring' : ''}>
        <ToolCard {block} {chatId} agentSession={turn.agentSession} lazy={history} />
      </div>
    {:else}
      <p class="whitespace-pre-line">{#each block.text.split(/((?:call:\d+|fact:[\w:./-]+))/g) as part}{#if /^(?:call:\d+|fact:)/.test(part)}<RefLink ref={part} onCall={scrollToCall} />{:else}{part}{/if}{/each}</p>
    {/if}
  {/each}
</div>
