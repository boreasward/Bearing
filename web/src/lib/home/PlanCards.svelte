<script lang="ts">
  import * as Collapsible from '$lib/ui/collapsible/index'
  import Section from '$lib/ui/domain/Section.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import { get } from '$lib/api'
  import { Button } from '$lib/ui/button/index'
  import PlanCard from './PlanCard.svelte'
  import TradeForm from './TradeForm.svelte'
  import { errorText, type PlanCardData } from './types'

  // 计划与持仓（spec §12）：三组——持仓、待入场、已结束（缺省折叠）。每组一个接口调用；
  // `version` 变化（页面里任何一处写入完成）时整块重取。
  let { version = 0, onChanged }: { version?: number; onChanged?: () => void } = $props()

  type GroupState = { state: 'loading' | 'empty' | 'ready' | 'error'; plans: PlanCardData[]; error: string }
  const empty: GroupState = { state: 'loading', plans: [], error: '' }
  let groups = $state<Record<'holdings' | 'pending' | 'ended', GroupState>>({
    holdings: { ...empty },
    pending: { ...empty },
    ended: { ...empty },
  })
  let endedOpen = $state(false)
  // 补录持仓：已有的持仓没有计划，复用记成交表单、不带 plan_id，宿主据此补录一份计划（spec §6.6）
  let adoptOpen = $state(false)

  async function loadGroup(group: 'holdings' | 'pending' | 'ended') {
    groups[group] = { ...empty }
    try {
      const data = await get<{ plans: PlanCardData[] }>(`/api/plans?group=${group}&limit=20`, { errorDetails: true })
      groups[group] = { state: data.plans.length ? 'ready' : 'empty', plans: data.plans, error: '' }
    } catch (cause) {
      groups[group] = { state: 'error', plans: [], error: errorText(cause) }
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

<TradeForm bind:open={adoptOpen} onDone={onChanged} />
