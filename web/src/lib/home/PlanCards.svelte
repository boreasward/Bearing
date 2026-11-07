<script lang="ts">
  import * as Collapsible from '$lib/ui/collapsible/index'
  import Section from '$lib/ui/domain/Section.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import { get } from '$lib/api'
  import PlanCard from './PlanCard.svelte'
  import type { PlanCardData } from './types'

  // 计划与持仓（spec §12）：三组——持仓、待入场、已结束（缺省折叠）。每组一个接口调用；
  // `version` 变化（页面里任何一处写入完成）时整块重取。
  let { version = 0, onChanged }: { version?: number; onChanged?: () => void } = $props()

  type GroupState = { state: 'loading' | 'ready' | 'error'; plans: PlanCardData[]; error: string }
  const empty: GroupState = { state: 'loading', plans: [], error: '' }
  let groups = $state<Record<'holdings' | 'pending' | 'ended', GroupState>>({
    holdings: { ...empty },
    pending: { ...empty },
    ended: { ...empty },
  })
  let endedOpen = $state(false)

  async function loadGroup(group: 'holdings' | 'pending' | 'ended') {
    groups[group] = { ...empty }
    try {
      const data = await get<{ plans: PlanCardData[] }>(`/api/plans?group=${group}&limit=20`)
      groups[group] = { state: 'ready', plans: data.plans, error: '' }
    } catch (cause) {
      groups[group] = { state: 'error', plans: [], error: cause instanceof Error ? cause.message : String(cause) }
    }
  }

  function load() {
    for (const group of ['holdings', 'pending', 'ended'] as const) loadGroup(group)
  }

  $effect(() => {
    void version
    load()
  })
</script>

<Section title="计划与持仓" level={2}>
  <div class="grid gap-4">
    {#snippet group(title: string, group: 'holdings' | 'pending' | 'ended', emptyText: string)}
      <section class="grid gap-2" aria-label={title}>
        <h3 class="text-base font-medium">{title}</h3>
        <DataBlock state={groups[group].state} message={groups[group].state === 'error' ? groups[group].error : emptyText} skeleton="row">
          {#if groups[group].plans.length}
            <div class="grid gap-3">
              {#each groups[group].plans as card (card.plan_id)}
                <PlanCard {card} {onChanged} />
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
                  <PlanCard {card} {onChanged} />
                {/each}
              </div>
            {/if}
          </DataBlock>
        </Collapsible.Content>
      {/if}
    </Collapsible.Root>
  </div>
</Section>
