<script lang="ts">
  import { onMount, untrack, type Snippet } from 'svelte'
  import * as Sheet from '$lib/ui/sheet/index'
  import { parseHash } from '$lib/router'
  import type { ChatListItem, TodayCounts } from '$lib/stores/types'
  import { layout, toggleDrawer, type DrawerPane } from './layout.svelte'
  import LeftRail from './LeftRail.svelte'
  import TopBar from './TopBar.svelte'
  import Drawer from './Drawer.svelte'

  let { title, session, counts, chats, currentChatId, onNewChat, onOpenSettings, center, drawerPane }: {
    title: string; session: string | null; counts: TodayCounts | null; chats: ChatListItem[]; currentChatId: string | null
    onNewChat(): void; onOpenSettings(): void; center: Snippet; drawerPane: Snippet<[pane: DrawerPane]>
  } = $props()
  let route = $state(parseHash(typeof location === 'undefined' ? '' : location.hash))
  let navOpen = $state(false)
  let sheetTrigger: HTMLElement | null = null
  let sheetKind = $state<'nav' | 'drawer'>('drawer')
  let sheetPane = $state<DrawerPane>('dash')
  const titles = { dash: '仪表', market: '行情', jobs: '定时任务' }

  function closeDrawer() {
    if (layout.drawer !== 'closed') toggleDrawer(layout.drawer)
  }
  function openNav(trigger: HTMLElement) {
    sheetTrigger = trigger
    closeDrawer()
    sheetKind = 'nav'
    navOpen = true
  }
  function openDrawer(pane: DrawerPane, trigger: HTMLElement) {
    sheetTrigger = trigger
    navOpen = false
    toggleDrawer(pane)
  }
  $effect(() => {
    if (layout.narrow && layout.drawer !== 'closed') {
      const pane = layout.drawer
      untrack(() => { navOpen = false; sheetKind = 'drawer'; sheetPane = pane })
    }
    if (!layout.narrow) untrack(() => { navOpen = false })
  })
  onMount(() => {
    const hashChanged = () => { route = parseHash(location.hash) }
    window.addEventListener('hashchange', hashChanged)
    let query: MediaQueryList | undefined
    try { query = window.matchMedia?.('(max-width: 1023px)') } catch { query = undefined }
    const resize = () => { layout.narrow = Boolean(query?.matches) }
    resize()
    query?.addEventListener?.('change', resize)
    return () => {
      window.removeEventListener('hashchange', hashChanged)
      query?.removeEventListener?.('change', resize)
    }
  })
</script>

{#snippet rail()}
  <LeftRail {counts} {chats} {currentChatId} {route} {onNewChat} onNavigate={() => { navOpen = false }} />
{/snippet}

<div class="flex h-dvh min-h-0 w-full overflow-hidden bg-background text-foreground">
  {#if !layout.narrow}<div class="shrink-0 border-r">{@render rail()}</div>{/if}
  <div class="flex min-w-0 flex-1 flex-col">
    <TopBar {title} {session} onToggleDrawer={openDrawer} onOpenNav={openNav} {onOpenSettings} />
    <div class="flex min-h-0 flex-1">
      <div class="min-w-0 flex-1 overflow-y-auto bg-card">{@render center()}</div>
      {#if !layout.narrow}<Drawer {drawerPane} />{/if}
    </div>
  </div>
</div>

{#if layout.narrow}
  <Sheet.Root open={navOpen || layout.drawer !== 'closed'} onOpenChange={(open) => { if (!open) { navOpen = false; closeDrawer() } }}>
    <Sheet.Content side={sheetKind === 'nav' ? 'left' : 'right'} class="gap-0"
      onCloseAutoFocus={(event) => { event.preventDefault(); sheetTrigger?.focus() }} aria-describedby={undefined}>
      {#if sheetKind === 'nav'}
        <Sheet.Title class="sr-only">导航</Sheet.Title>
        {@render rail()}
      {:else}
        <Sheet.Header><Sheet.Title class="font-serif font-semibold">{titles[sheetPane]}侧览</Sheet.Title></Sheet.Header>
        <div class="p-3">{@render drawerPane(sheetPane)}</div>
      {/if}
    </Sheet.Content>
  </Sheet.Root>
{/if}
