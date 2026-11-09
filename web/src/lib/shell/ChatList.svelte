<script lang="ts">
  import { Button } from '$lib/ui/button/index'
  import Icon from '$lib/ui/domain/Icon.svelte'
  import { hrefOf } from '$lib/router'
  import type { ChatListItem } from '$lib/stores/types'
  import { layout } from './layout.svelte'
  let { chats, currentChatId, onNewChat, onNavigate }: {
    chats: ChatListItem[]; currentChatId: string | null; onNewChat(): void; onNavigate?: () => void
  } = $props()

  function groups(rows: ChatListItem[]) {
    const day = (value: number) => new Date(value + 8 * 60 * 60 * 1000).toISOString().slice(0, 10)
    const today = day(Date.now())
    const weekday = new Date(`${today}T00:00:00Z`).getUTCDay()
    const monday = new Date(Date.parse(`${today}T00:00:00Z`) - ((weekday + 6) % 7) * 86400000).toISOString().slice(0, 10)
    const grouped = ['今天', '本周', '更早'].map(label => ({ label, rows: [] as ChatListItem[] }))
    for (const chat of [...rows].sort((a, b) => Date.parse(b.last_message_at) - Date.parse(a.last_message_at))) {
      const stamp = Date.parse(chat.last_message_at)
      const date = Number.isFinite(stamp) ? day(stamp) : ''
      grouped[date === today ? 0 : date >= monday && date < today ? 1 : 2].rows.push(chat)
    }
    return grouped.filter(group => group.rows.length)
  }
</script>
<div class="grid gap-2 p-2">
  <Button variant="ghost" size={layout.navCollapsed ? 'icon' : 'md'} aria-label="新开对话" title="新开对话" onclick={() => { onNewChat(); onNavigate?.() }}>
    <Icon name="plus" />{#if !layout.navCollapsed}新开对话{/if}
  </Button>
  {#if !layout.navCollapsed}
    <nav aria-label="对话列表" class="grid gap-3">
      {#each groups(chats) as group (group.label)}
        <section aria-label={group.label}>
          <h2 class="px-2 py-1 text-xs text-muted-foreground">{group.label}</h2>
          {#each group.rows as chat (chat.chat_id)}
            <a href={hrefOf({ kind: 'chat', chatId: chat.chat_id })} aria-current={currentChatId === chat.chat_id ? 'page' : undefined}
              onclick={onNavigate} class="flex items-center gap-1 rounded-md px-2 py-2 text-sm text-foreground hover:bg-muted {currentChatId === chat.chat_id ? 'bg-muted' : ''}">
              <span class="min-w-0 flex-1 truncate" title={chat.title || '新对话'}>{chat.title || '新对话'}</span>
              {#if chat.read_only}<span class="shrink-0 text-xs text-muted-foreground">只读</span>{/if}
            </a>
          {/each}
        </section>
      {/each}
      {#if !chats.length}<p class="px-2 text-xs text-muted-foreground">还没有对话</p>{/if}
    </nav>
  {/if}
</div>
