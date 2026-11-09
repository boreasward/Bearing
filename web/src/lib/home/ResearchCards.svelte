<script lang="ts">
  import * as Collapsible from '$lib/ui/collapsible/index'
  import Section from '$lib/ui/domain/Section.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import { Button } from '$lib/ui/button/index'
  import { get } from '$lib/api'
  import ResearchCard from './ResearchCard.svelte'
  import { errorText, type ResearchCardData } from './types'

  import { research, invalidateAll } from '$lib/stores'
  import { actionCompleted } from '$lib/views/context'
  let { id = null, onChanged, onStartResearch }: { id?: number | null; onChanged?: () => void; onStartResearch?: (text: string) => void } = $props()
  const resources = { watching: research('watching'), ended: research('ended') }
  const groups = $derived(Object.fromEntries(Object.entries(resources).map(([key, resource]) => {
    const cards = resource.data?.research ?? []
    return [key, { state: resource.state === 'ready' && !cards.length ? 'empty' as const : resource.state, cards, error: resource.error }]
  })))
  let endedOpen = $state(false)
  let selected = $state<ResearchCardData | null>(null)
  const selectedCard = $derived(selected?.research_id === id ? selected : null)
  let selectedError = $state('')
  $effect(() => {
    const requested = id
    const snapshots = Object.values(groups)
    if (requested === null) { selected = null; selectedError = ''; return }
    if (snapshots.some(group => group.state === 'loading')) return
    const existing = snapshots.flatMap(group => group.cards).find(card => card.research_id === requested)
    if (existing) {
      selected = null
      const card = existing
      if (card.status !== 'watching') endedOpen = true
      return
    }
    let alive = true
    selectedError = ''
    get<ResearchCardData>(`/api/research/${requested}`, { errorDetails: true }).then(card => {
      if (!alive) return
      selected = card
      if (card.status !== 'watching') endedOpen = true
    }).catch(cause => { if (alive) selectedError = errorText(cause) })
    return () => { alive = false }
  })
  function changed() { invalidateAll(); actionCompleted('已更新研究'); onChanged?.() }
</script>

<Section title="研究" level={2}>
  <div class="grid gap-4">
    {#if id !== null && !Object.values(groups).some(group => group.cards.some(card => card.research_id === id))}
      <DataBlock state={selectedError ? 'error' : selectedCard ? 'ready' : 'loading'} message={selectedError}>
        {#if selectedCard}{#key selectedCard}<ResearchCard card={selectedCard} expanded onChanged={changed} />{/key}{/if}
      </DataBlock>
    {/if}
    <section class="grid gap-2" aria-label="观察中">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h3 class="text-base font-medium">观察中</h3>
        <Button size="sm" variant="outline" onclick={() => onStartResearch?.('研究 ')}>发起研究</Button>
      </div>
      <DataBlock
        state={groups.watching.state}
        message={groups.watching.state === 'error' ? groups.watching.error : '暂无观察中的研究'}
        skeleton="row"
      >
        {#if groups.watching.cards.length}
          <div class="grid gap-3">
            {#each groups.watching.cards as card (card.research_id)}
              <ResearchCard {card} expanded={card.research_id === id} onChanged={changed} />
            {/each}
          </div>
        {/if}
      </DataBlock>
    </section>

    <Collapsible.Root bind:open={endedOpen}>
      <Collapsible.Trigger class="text-base font-medium underline-offset-2 hover:underline">已结束</Collapsible.Trigger>
      {#if endedOpen}
        <Collapsible.Content forceMount class="grid gap-2 pt-1">
          <DataBlock
            state={groups.ended.state}
            message={groups.ended.state === 'error' ? groups.ended.error : '暂无已结束的研究'}
            skeleton="row"
          >
            {#if groups.ended.cards.length}
              <div class="grid gap-3">
                {#each groups.ended.cards as card (card.research_id)}
                  <ResearchCard {card} expanded={card.research_id === id} onChanged={changed} />
                {/each}
              </div>
            {/if}
          </DataBlock>
        </Collapsible.Content>
      {/if}
    </Collapsible.Root>
  </div>
</Section>
