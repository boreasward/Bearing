<script lang="ts">
  import { Button } from '$lib/ui/button/index'
  import Icon from '$lib/ui/domain/Icon.svelte'
  import type { Route } from '$lib/router'
  import type { ChatListItem, TodayCounts } from '$lib/stores/types'
  import { layout, toggleNav } from './layout.svelte'
  import SegmentSwitch from './SegmentSwitch.svelte'
  import QuickEntries from './QuickEntries.svelte'
  import ChatList from './ChatList.svelte'
  let { counts, chats, currentChatId, route, onNewChat, onNavigate }: {
    counts: TodayCounts | null; chats: ChatListItem[]; currentChatId: string | null; route: Route
    onNewChat(): void; onNavigate?: () => void
  } = $props()
</script>
<aside aria-label="左栏" class="flex h-full shrink-0 flex-col overflow-y-auto bg-background {layout.navCollapsed ? 'w-[56px]' : 'w-[208px]'}">
  <div class="flex gap-1 border-b p-2 {layout.navCollapsed ? 'flex-col items-center' : 'items-center justify-between'}">
    <SegmentSwitch />
    <Button variant="ghost" size="icon" aria-label={layout.navCollapsed ? '展开左栏' : '折叠左栏'} title={layout.navCollapsed ? '展开左栏' : '折叠左栏'} aria-pressed={layout.navCollapsed} onclick={toggleNav}>
      <Icon name="panel-left" />
    </Button>
  </div>
  {#if layout.navSegment === 'quick'}
    <QuickEntries {counts} {route} {onNavigate} />
  {:else}
    <ChatList {chats} {currentChatId} {onNewChat} {onNavigate} />
  {/if}
</aside>
