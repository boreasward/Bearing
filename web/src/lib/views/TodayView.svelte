<script lang="ts">
  import * as Select from '$lib/ui/select/index'
  import { Alert, AlertDescription } from '$lib/ui/alert/index'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import PageHeader from '$lib/ui/domain/PageHeader.svelte'
  import TodayActions from '$lib/home/TodayActions.svelte'
  import AccountStrip from '$lib/home/AccountStrip.svelte'
  import Markdown from '$lib/home/Markdown.svelte'
  import { today, market, briefs, briefFor, invalidateAll } from '$lib/stores'
  import PageCard from './PageCard.svelte'
  import TodayColumns from './TodayColumns.svelte'
  import ResourceBlock from './ResourceBlock.svelte'
  import MarketCard from './cards/MarketCard.svelte'
  import type { BriefSection } from '$lib/home/types'
  import { actionCompleted } from './context'
  let { session = null }: { session?: string | null } = $props()
  let path = $derived(session ?? 'latest')
  let selected = $state('')
  const resource = $derived(briefFor(path))
  const list = $derived(briefs.state === 'ready' ? briefs.data?.briefs ?? [] : [])
  const current = $derived(resource.state === 'ready' ? resource.data : null)
  const absentBrief = $derived(resource.state === 'error' && ['今天还没有简报', '还没有运行记录'].includes(resource.error))
  const isLatest = $derived(path === 'latest' || absentBrief || (list.length > 0 && current?.session === list[0].session))
  const sections = $derived(current?.sections ?? [])
  const sectionMap = $derived(new Map(sections.map(section => [section.key, section])))
  const orderedKeys = ['summary', 'changes', 'holdings', 'pending', 'research', 'candidates', 'unresolved']
  const visibleSections = $derived([
    ...orderedKeys.flatMap(key => sectionMap.has(key) ? [sectionMap.get(key)!] : []),
    ...sections.filter(section => !orderedKeys.includes(section.key) && !['actions', 'market', 'account'].includes(section.key)),
  ])
  $effect(() => { if (current) selected = current.session })
  const actions = $derived((today.data?.actions ?? []).map(action => ({ ...action, quantity: Number(action.quantity) })))
  const sectionTitles: Record<string, string> = { candidates: '规则名单' }
  function briefErrorText(error: unknown): string {
    const message = error instanceof Error ? error.message : String(error)
    if (message === 'no_brief') return '今天还没有简报'
    if (message === 'no_run') return '还没有运行记录'
    return message.includes(': ') ? message.split(': ').slice(1).join(': ') : message
  }
  function switchTo(value: string) {
    // bits-ui writes `selected` before onValueChange; compare with the brief actually loaded.
    if (value && value !== current?.session) path = value
  }
  function changed() { invalidateAll(); actionCompleted('已更新今日操作') }
  const actionsSection = $derived(sectionMap.get('actions'))
  const marketSection = $derived(sectionMap.get('market'))
  const hasLegacyMarkdown = $derived(current?.sections === undefined && Boolean(current?.markdown))
  const marketEmptyMessage = $derived(market.error === '还没有运行记录' ? '还没有运行记录' : market.error ? briefErrorText(market.error) : '这一天没有市场数据')
</script>
<div class="grid gap-4 p-4">
  <PageHeader level={1} title="今日">
    {#snippet actions()}
      {#if list.length > 1}
        <Select.Root type="single" bind:value={selected} onValueChange={switchTo}>
          <Select.Trigger aria-label="选择简报" size="sm">{selected}</Select.Trigger>
          <Select.Content>{#each list as item (item.session)}<Select.Item value={item.session} label={`${item.session}${item.status !== 'final' ? '（草稿）' : ''}`} />{/each}</Select.Content>
        </Select.Root>
      {/if}
    {/snippet}
  </PageHeader>
  <TodayColumns>
      {#snippet aside()}
  <AccountStrip tag={isLatest ? undefined : '最新'} />
        {#if current?.market}
          <MarketCard data={current.market} brief />
        {:else if marketSection}
          <PageCard title="行情"><Markdown source={marketSection.markdown} /></PageCard>
        {:else if current}
          <PageCard title="行情"><p class="text-sm text-muted-foreground">这一天没有市场数据</p></PageCard>
        {:else if !absentBrief}
          <PageCard title="行情"><DataBlock state={resource.state === 'loading' ? 'loading' : resource.state === 'error' ? 'error' : 'empty'} message={resource.error || '这一天没有市场数据'} skeleton="block" /></PageCard>
        {:else if market.state === 'ready' && market.data}
          <MarketCard data={market.data} brief />
        {:else}
          <PageCard title="行情"><DataBlock state={market.state === 'loading' ? 'loading' : market.state === 'error' ? 'empty' : 'empty'} message={marketEmptyMessage} skeleton="block" /></PageCard>
        {/if}
      {/snippet}
      {#snippet children()}
        {#if resource.state === 'loading' && !current}
          <DataBlock state="loading" skeleton="block" />
        {:else}
          <div class="grid gap-4">

          {#if current?.status === 'draft'}
            <Alert variant="warn"><AlertDescription>二次确认未完成</AlertDescription></Alert>
          {/if}
          {#if current?.status === 'error'}
            <Alert variant="danger"><AlertDescription>{current.lead ?? briefErrorText(current.error)}</AlertDescription></Alert>
          {:else if current?.error}
            <Alert variant="danger"><AlertDescription>{briefErrorText(current.error)}</AlertDescription></Alert>
          {/if}
          {#if current?.status !== 'error' && current?.lead}
            <p class="text-sm text-muted-foreground">{current.lead}</p>
          {/if}
          {#if isLatest}
            <ResourceBlock resource={today} emptyMessage="暂无今日操作"><TodayActions {actions} onChanged={changed} /></ResourceBlock>
          {:else}
            <PageCard title="今日操作">{#if actionsSection}<Markdown source={actionsSection.markdown} />{:else}<p class="text-sm text-muted-foreground">当天没有需要操作的事项</p>{/if}</PageCard>
          {/if}
          {#if hasLegacyMarkdown}
            <PageCard title="简报"><Markdown source={current!.markdown!} /></PageCard>
          {:else if current}
            {#each visibleSections as section (section.key)}
              <PageCard title={sectionTitles[section.key] ?? section.title}><Markdown source={section.markdown} /></PageCard>
            {/each}
          {:else if resource.state === 'error'}
            <PageCard title="简报"><p class="text-sm text-muted-foreground">{briefErrorText(resource.error)}</p></PageCard>
          {/if}
          {#if current?.footer}<p class="text-xs text-muted-foreground">{current.footer}</p>{/if}
          </div>
        {/if}
      {/snippet}
    </TodayColumns>
</div>
