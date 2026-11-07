<script lang="ts">
  import { onMount } from 'svelte'
  import * as Select from '$lib/ui/select/index'
  import { Alert, AlertDescription } from '$lib/ui/alert/index'
  import Section from '$lib/ui/domain/Section.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import { get } from '$lib/api'
  import Markdown from './Markdown.svelte'
  import type { Brief, BriefListItem } from './types'

  // 今日简报（spec §12）：当天的最终简报；二次确认没完成时显示草稿并写明；没有时显示原因。
  // 可翻看历史：GET /api/briefs 列出，选中后取 GET /api/briefs/{session}。

  let list = $state<BriefListItem[]>([])
  let current = $state<Brief | null>(null)
  let blockState = $state<'loading' | 'ready' | 'empty' | 'error'>('loading')
  let message = $state('')
  let selected = $state('')

  function briefErrorText(error: unknown): string {
    const message = error instanceof Error ? error.message : String(error)
    if (message === 'no_brief') return '今天还没有简报'
    if (message === 'no_run') return '还没有运行记录，简报会在第一次运行后出现'
    return message.includes(': ') ? message.split(': ').slice(1).join(': ') : message
  }

  async function loadBrief(path: 'latest' | string) {
    blockState = 'loading'
    message = ''
    try {
      current = await get<Brief>(`/api/briefs/${path}`, { errorDetails: true })
      selected = current.session
      blockState = 'ready'
    } catch (cause) {
      current = null
      blockState = 'empty'
      message = briefErrorText(cause)
    }
  }

  onMount(async () => {
    try {
      const data = await get<{ briefs: BriefListItem[] }>('/api/briefs?limit=30')
      list = data.briefs
    } catch {
      list = []
    }
    await loadBrief('latest')
  })

  function switchTo(session: string) {
    if (session && session !== selected) loadBrief(session)
  }
</script>

<Section title="今日简报" level={2}>
  {#snippet actions()}
    {#if list.length > 1}
      <Select.Root type="single" bind:value={selected} onValueChange={switchTo}>
        <Select.Trigger aria-label="选择简报" size="sm">{selected}</Select.Trigger>
        <Select.Content>
          {#each list as item (item.session)}
            <Select.Item value={item.session} label={`${item.session}${item.status !== 'final' ? '（草稿）' : ''}`} />
          {/each}
        </Select.Content>
      </Select.Root>
    {/if}
  {/snippet}
  <DataBlock state={blockState} {message} skeleton="block">
    {#if current}
      <div class="grid gap-2">
        {#if current.unfinished}
          <Alert variant="warn"><AlertDescription>二次确认未完成</AlertDescription></Alert>
        {/if}
        {#if current.error}
          <Alert variant="danger"><AlertDescription>{briefErrorText(current.error)}</AlertDescription></Alert>
        {/if}
        {#if current.markdown}
          <Markdown source={current.markdown} />
        {/if}
      </div>
    {/if}
  </DataBlock>
</Section>
