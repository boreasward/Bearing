<script lang="ts">
  import type { Snippet } from 'svelte'
  import { Button } from '$lib/ui/button/index'
  import Icon from '$lib/ui/domain/Icon.svelte'
  import { useNarrow } from '$lib/ui/narrow.svelte'
  import * as Sheet from '$lib/ui/sheet/index'
  import { toggleTheme } from '../theme'
  import SideNav from './SideNav.svelte'
  import { createEventStream } from '../sse'

  let { route, sub, children }: { route: string; sub?: string; children?: Snippet } = $props()

  const events = createEventStream('/api/events')
  let lastEvent = $state<string | null>(null)
  $effect(() => {
    const ev = $events
    if (ev) lastEvent = `${ev.kind ?? ev.status} — ${ev.run_id?.slice(0, 8)}`
  })

  // The top bar names the current page; the content column is 1400px wide, table pages may use the whole width.
  const TITLES: Record<string, string> = {
    today: '今日', instrument: '标的', account: '账户', review: '复盘', data: '数据', knowledge: '知识', tools: '工具', settings: '设置',
    plan: '计划', discuss: '讨论',
  }
  const FULL_WIDTH = ['today', 'account', 'review']
  const pageTitle = $derived(TITLES[route] ?? '')

  // Below 768px the side navigation becomes a drawer from the top bar (spec 4.1: 顶部抽屉); it closes when the page changes.
  const narrow = useNarrow()
  let menuOpen = $state(false)
  $effect(() => {
    void route
    void sub
    menuOpen = false
  })
</script>

<div class="flex h-screen overflow-hidden bg-background text-foreground">
  {#if !narrow.current}<SideNav current={route} {sub} />{/if}
  <div class="flex min-w-0 flex-1 flex-col">
    <header class="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-border bg-card px-4 shadow-1">
      <div class="flex min-w-0 items-center gap-2">
        {#if narrow.current}
          <Button variant="ghost" size="icon" aria-label="打开菜单" onclick={() => (menuOpen = true)}><Icon name="menu" /></Button>
        {/if}
        <span class="truncate text-base font-medium">{pageTitle}</span>
      </div>
      <div class="flex items-center gap-3 text-sm text-muted-foreground">
        {#if lastEvent}<span class="flex items-center gap-1 text-xs"><Icon name="bell" /><span>{lastEvent}</span></span>{/if}
        <Button variant="ghost" size="icon" aria-label="切换主题" onclick={toggleTheme}><Icon name="sun-moon" /></Button>
      </div>
    </header>
    <main class="flex-1 overflow-y-auto p-4">
      <div class={FULL_WIDTH.includes(route) ? 'w-full' : 'mx-auto w-full max-w-[1400px]'}>
        {#if children}
          {@render children()}
        {/if}
      </div>
    </main>
  </div>
</div>

<Sheet.Root bind:open={menuOpen}>
  <Sheet.Content side="top" showCloseButton={false} class="max-h-[85vh] gap-0 p-0">
    <Sheet.Header class="sr-only"><Sheet.Title>导航菜单</Sheet.Title><Sheet.Description>切换页面</Sheet.Description></Sheet.Header>
    <SideNav current={route} {sub} collapsible={false} />
  </Sheet.Content>
</Sheet.Root>
