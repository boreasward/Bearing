<script lang="ts">
  import * as Collapsible from '$lib/ui/collapsible/index'
  import Section from '$lib/ui/domain/Section.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import { get } from '$lib/api'
  import { Button } from '$lib/ui/button/index'
  import PlanCard from './PlanCard.svelte'
  import TradeForm from './TradeForm.svelte'
  import { errorText, type PlanCardData } from './types'

  import { plans, invalidateAll } from '$lib/stores'
  import type { PlanCardData as StoredPlan } from '$lib/stores/types'
  import { actionCompleted } from '$lib/views/context'
  let { id = null, onChanged }: { id?: number | null; onChanged?: () => void } = $props()
  const resources = { holdings: plans('holdings'), pending: plans('pending'), ended: plans('ended') }
  const groups = $derived(Object.fromEntries(Object.entries(resources).map(([key, resource]) => {
    const rows = resource.data?.plans ?? []
    return [key, { state: resource.state === 'ready' && !rows.length ? 'empty' as const : resource.state, plans: rows.map(displayPlan), error: resource.error }]
  })))
  function displayPlan(card: StoredPlan): PlanCardData {
    return { ...card, position: card.position ? { ...card.position, quantity: Number(card.position.quantity) } : null }
  }
  let endedOpen = $state(false)
  let adoptOpen = $state(false)
  let selected = $state<PlanCardData | null>(null)
  const selectedCard = $derived(selected?.plan_id === id ? selected : null)
  let selectedError = $state('')
  $effect(() => {
    const requested = id
    const snapshots = Object.values(groups)
    if (requested === null) { selected = null; selectedError = ''; return }
    if (snapshots.some(group => group.state === 'loading')) return
    const existing = snapshots.flatMap(group => group.plans).find(card => card.plan_id === requested)
    if (existing) {
      selected = null
      const card = existing
      if (!['pending', 'open'].includes(card.status)) endedOpen = true
      return
    }
    let alive = true
    selectedError = ''
    get<StoredPlan>(`/api/plans/${requested}`, { errorDetails: true }).then(card => {
      if (!alive) return
      selected = displayPlan(card)
      if (!['pending', 'open'].includes(card.status)) endedOpen = true
    }).catch(cause => { if (alive) selectedError = errorText(cause) })
    return () => { alive = false }
  })
  function changed() { invalidateAll(); actionCompleted('已更新计划与持仓'); onChanged?.() }
</script>

<Section title="计划与持仓" level={2}>
  <div class="grid gap-4">
    {#if id !== null && !Object.values(groups).some(group => group.plans.some(card => card.plan_id === id))}
      <DataBlock state={selectedError ? 'error' : selectedCard ? 'ready' : 'loading'} message={selectedError}>
        {#if selectedCard}{#key selectedCard}<PlanCard card={selectedCard} expanded onChanged={changed} />{/key}{/if}
      </DataBlock>
    {/if}
    {#snippet group(title: string, group: 'holdings' | 'pending' | 'ended', emptyText: string)}
      <section class="grid gap-2" aria-label={title}>
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h3 class="text-base font-medium">{title}</h3>
          {#if group === 'holdings'}
            <Button size="sm" variant="outline" onclick={() => (adoptOpen = true)}>补录持仓</Button>
          {/if}
        </div>
        <DataBlock state={groups[group].state} message={groups[group].state === 'error' ? groups[group].error : emptyText} skeleton="row">
          {#if groups[group].plans.length}
            <div class="grid gap-3">
              {#each groups[group].plans as card (card.plan_id)}
                <PlanCard {card} expanded={card.plan_id === id} onChanged={changed} />
              {/each}
            </div>
          {/if}
        </DataBlock>
      </section>
    {/snippet}

    {@render group('持仓', 'holdings', '暂无持仓')}
    {@render group('待入场', 'pending', '暂无待入场计划')}

    <Collapsible.Root bind:open={endedOpen}>
      <Collapsible.Trigger class="text-base font-medium underline-offset-2 hover:underline">已结束</Collapsible.Trigger>
      {#if endedOpen}
        <Collapsible.Content forceMount class="grid gap-2 pt-1">
          <DataBlock state={groups.ended.state} message={groups.ended.state === 'error' ? groups.ended.error : '暂无已结束计划'} skeleton="row">
            {#if groups.ended.plans.length}
              <div class="grid gap-3">
                {#each groups.ended.plans as card (card.plan_id)}
                  <PlanCard {card} expanded={card.plan_id === id} onChanged={changed} />
                {/each}
              </div>
            {/if}
          </DataBlock>
        </Collapsible.Content>
      {/if}
    </Collapsible.Root>
  </div>
</Section>

<TradeForm bind:open={adoptOpen} onDone={changed} />
