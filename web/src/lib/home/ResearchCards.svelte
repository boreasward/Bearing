<script lang="ts">
  import * as Collapsible from '$lib/ui/collapsible/index'
  import Section from '$lib/ui/domain/Section.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import { Button } from '$lib/ui/button/index'
  import { get } from '$lib/api'
  import ResearchCard from './ResearchCard.svelte'
  import { errorText, type ResearchCardData } from './types'

  // 研究（spec §10 研究块）：两组——观察中、已结束（缺省折叠）。每组一个接口调用；
  // `version` 变化（页面里任何一处写入完成）时整块重取。「发起研究」把预填句交给页面
  // 去打开助手面板（研究在对话里发起，spec §4「研究从哪里发起」）。
  let { version = 0, onChanged, onStartResearch }: {
    version?: number
    onChanged?: () => void
    onStartResearch?: (text: string) => void
  } = $props()

  type GroupState = { state: 'loading' | 'empty' | 'ready' | 'error'; cards: ResearchCardData[]; error: string }
  const empty: GroupState = { state: 'loading', cards: [], error: '' }
  let groups = $state<Record<'watching' | 'ended', GroupState>>({ watching: { ...empty }, ended: { ...empty } })
  let endedOpen = $state(false)

  async function loadGroup(group: 'watching' | 'ended') {
    groups[group] = { ...empty }
    try {
      const data = await get<{ research: ResearchCardData[] }>(`/api/research?group=${group}&limit=20`, { errorDetails: true })
      groups[group] = { state: data.research.length ? 'ready' : 'empty', cards: data.research, error: '' }
    } catch (cause) {
      groups[group] = { state: 'error', cards: [], error: errorText(cause) }
    }
  }

  $effect(() => {
    void version
    loadGroup('watching')
    loadGroup('ended')
  })
</script>

<Section title="研究" level={2}>
  <div class="grid gap-4">
    <section class="grid gap-2" aria-label="观察中">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h3 class="text-base font-medium">观察中</h3>
        <Button size="sm" variant="outline" onclick={() => onStartResearch?.('研究 ：')}>发起研究</Button>
      </div>
      <DataBlock
        state={groups.watching.state}
        message={groups.watching.state === 'error' ? groups.watching.error : '暂无观察中的研究'}
        skeleton="row"
      >
        {#if groups.watching.cards.length}
          <div class="grid gap-3">
            {#each groups.watching.cards as card (card.research_id)}
              <ResearchCard {card} {onChanged} />
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
                  <ResearchCard {card} {onChanged} />
                {/each}
              </div>
            {/if}
          </DataBlock>
        </Collapsible.Content>
      {/if}
    </Collapsible.Root>
  </div>
</Section>
