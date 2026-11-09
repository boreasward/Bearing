<script lang="ts">
  import * as Select from '$lib/ui/select/index'
  import { Alert, AlertDescription } from '$lib/ui/alert/index'
  import Section from '$lib/ui/domain/Section.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import { briefs, briefFor } from '$lib/stores'
  import Markdown from './Markdown.svelte'

  // 今日简报（spec §12）：当天的最终简报；二次确认没完成时显示草稿并写明；没有时显示原因。
  // 可翻看历史：GET /api/briefs 列出，选中后取 GET /api/briefs/{session}。

  let { session = null }: { session?: string | null } = $props()
  let path = $state('latest')
  $effect(() => { path = session ?? 'latest' })
  const resource = $derived(briefFor(path))
  const list = $derived(briefs.data?.briefs ?? [])
  const current = $derived(resource.data)
  const blockState = $derived(resource.state === 'error' ? 'empty' : resource.state)
  const message = $derived(resource.error === '还没有运行记录' ? '还没有运行记录，简报会在第一次运行后出现' : resource.error)
  let selected = $state('')
  $effect(() => { if (current) selected = current.session })

  function briefErrorText(error: unknown): string {
    const message = error instanceof Error ? error.message : String(error)
    if (message === 'no_brief') return '今天还没有简报'
    if (message === 'no_run') return '还没有运行记录，简报会在第一次运行后出现'
    return message.includes(': ') ? message.split(': ').slice(1).join(': ') : message
  }

  function switchTo(session: string) {
    // bits-ui writes `selected` (the bindable) before calling onValueChange, so compare against
    // the actually-loaded brief, never against `selected`
    if (session && session !== current?.session) path = session
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
