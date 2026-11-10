<script lang="ts">
  import { onDestroy, onMount, tick, untrack, type Snippet } from 'svelte'
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
  let chatSheetDismissed = $state(false)
  let sheetContent = $state<HTMLDivElement | null>(null)
  const sheetOpen = $derived(!chatSheetDismissed && (navOpen || layout.drawer !== 'closed'))
  let chatClose: Promise<void> | null = null
  let resolveChatClose: (() => void) | null = null
  function finishChatClose() { resolveChatClose?.(); resolveChatClose = null }
  // Wait for presence completion; autofocus cleanup alone can leave the overlay mounted.
  export async function dismissForChat(): Promise<void> {
    if (!layout.narrow) return
    if (!chatClose) {
      chatClose = new Promise<void>(resolve => { resolveChatClose = resolve })
      navOpen = false
      chatSheetDismissed = true
      if (!sheetContent) finishChatClose()
    }
    await chatClose
    await tick()
    chatClose = null
  }
  function closeAutoFocus(event: Event) {
    event.preventDefault()
    if (!chatClose) sheetTrigger?.focus()
  }
  onDestroy(finishChatClose)
  const titles = { dash: '仪表', market: '行情', jobs: '定时任务', tools: '工具' }

  function closeDrawer() {
    if (layout.drawer !== 'closed') toggleDrawer(layout.drawer)
  }
  function openNav(trigger: HTMLElement) {
    chatSheetDismissed = false
    sheetTrigger = trigger
    closeDrawer()
    sheetKind = 'nav'
    navOpen = true
  }
  function openDrawer(pane: DrawerPane, trigger: HTMLElement) {
    const reopening = layout.narrow && chatSheetDismissed && layout.drawer === pane
    chatSheetDismissed = false
    sheetTrigger = trigger
    navOpen = false
    if (!reopening) toggleDrawer(pane)
  }
  $effect(() => {
    if (layout.narrow && layout.drawer !== 'closed') {
      const pane = layout.drawer
      untrack(() => { navOpen = false; sheetKind = 'drawer'; sheetPane = pane })
    }
    if (!layout.narrow) untrack(() => { navOpen = false; chatSheetDismissed = false; finishChatClose() })
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
    <TopBar drawerHidden={layout.narrow && chatSheetDismissed} {title} {session} onToggleDrawer={openDrawer} onOpenNav={openNav} {onOpenSettings} />
    <div class="flex min-h-0 flex-1">
      <div class="min-w-0 flex-1 overflow-y-auto bg-card">{@render center()}</div>
      {#if !layout.narrow}<Drawer {drawerPane} />{/if}
    </div>
  </div>
</div>

{#if layout.narrow}
  <Sheet.Root open={sheetOpen} onOpenChangeComplete={(open) => { if (!open) finishChatClose() }} onOpenChange={(open) => { if (!open) { navOpen = false; if (!chatSheetDismissed) closeDrawer() } }}>
    <Sheet.Content bind:ref={sheetContent} side={sheetKind === 'nav' ? 'left' : 'right'} class="gap-0"
      onCloseAutoFocus={closeAutoFocus} aria-describedby={undefined}>
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
