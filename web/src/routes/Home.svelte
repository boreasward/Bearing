<script lang="ts">
  import { onMount, tick } from 'svelte'
  import { parseHash, type ViewName } from '$lib/router'
  import { today, chats, invalidateAll } from '$lib/stores'
  import { BRING_EVENT, ACTION_EVENT, type BringEvent, type ActionEvent, type ContextChip } from '$lib/views/context'
  import Shell from '$lib/shell/Shell.svelte'
  import ChatPanel from '$lib/chat/ChatPanel.svelte'
  import DashboardPane from '$lib/drawer/DashboardPane.svelte'
  import MarketPane from '$lib/drawer/MarketPane.svelte'
  import JobsPane from '$lib/drawer/JobsPane.svelte'
  import ToolsPane from '$lib/drawer/ToolsPane.svelte'
  import TodayView from '$lib/views/TodayView.svelte'
  import AccountView from '$lib/views/AccountView.svelte'
  import MarketView from '$lib/views/MarketView.svelte'
  import PlansView from '$lib/views/PlansView.svelte'
  import ResearchView from '$lib/views/ResearchView.svelte'
  import RetrospectView from '$lib/views/RetrospectView.svelte'
  import JobsView from '$lib/views/JobsView.svelte'
  import SettingsSheet from '$lib/home/SettingsSheet.svelte'
  let hash = $state(location.hash)
  const route = $derived(parseHash(hash))
  const chatRows = $derived(chats.data?.chats ?? [])
  let recentChatId = $state<string | null>(null)
  const chatId = $derived(route.kind === 'chat' ? route.chatId ?? chatRows[0]?.chat_id ?? null : recentChatId ?? chatRows[0]?.chat_id ?? null)
  const session = $derived(new URLSearchParams(hash.split('?')[1] ?? '').get('session'))
  const titles: Record<ViewName, string> = { today:'今日', account:'账户', market:'行情', plans:'计划与持仓', research:'研究', retrospect:'复盘', jobs:'定时任务' }
  const title = $derived(route.kind === 'view' ? titles[route.view] : chatRows.find(row => row.chat_id === chatId)?.title || '新对话')
  let settingsOpen = $state(false)
  let shell: Shell | undefined = $state()
  let panel: ChatPanel | undefined = $state()
  let prefillChips = $state<ContextChip[] | undefined>()
  let prefillText = $state<{ text: string; seq: number } | undefined>()
  let prefillSeq = 0
  $effect(() => { if (route.kind === 'chat' && chatId) recentChatId = chatId })
  function navigate(hashValue: string) { hash = hashValue; location.hash = hashValue }
  async function openChat() {
    await shell?.dismissForChat()
    navigate(chatId ? `#chat/${encodeURIComponent(chatId)}` : '#')
    await tick(); await panel?.focusInput()
  }
  function bring(chip: ContextChip) { prefillChips = [chip]; void openChat() }
  function startResearch(text: string) { prefillText = { text, seq: ++prefillSeq }; void openChat() }
  async function newChat() {
    const id = await panel?.createChat()
    if (id) { recentChatId = id; navigate(`#chat/${encodeURIComponent(id)}`) }
  }
  onMount(() => {
    const hashChanged = () => { hash = location.hash }
    const brought = (event: Event) => bring((event as BringEvent).detail)
    const action = (event: Event) => panel?.appendFeedback((event as ActionEvent).detail.text)
    const key = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); void openChat() }
    }
    window.addEventListener('hashchange', hashChanged)
    window.addEventListener(BRING_EVENT, brought)
    window.addEventListener(ACTION_EVENT, action)
    window.addEventListener('keydown', key)
    return () => {
      window.removeEventListener('hashchange', hashChanged)
      window.removeEventListener(BRING_EVENT, brought)
      window.removeEventListener(ACTION_EVENT, action)
      window.removeEventListener('keydown', key)
    }
  })
</script>
<Shell bind:this={shell} {title} session={today.data?.session ?? null} counts={today.data?.counts ?? null} chats={chatRows} currentChatId={chatId}
  onNewChat={() => { void newChat() }} onOpenSettings={() => { settingsOpen = true }}>
  {#snippet center()}
    <div class="h-full" hidden={route.kind !== 'chat'}><ChatPanel bind:this={panel} {chatId} {prefillChips} {prefillText} onWrote={() => { recentChatId = chatId }} /></div>
    {#if route.kind === 'view'}
      {#if route.view === 'today'}<TodayView {session} />
      {:else if route.view === 'account'}<div class="p-4"><AccountView /></div>
      {:else if route.view === 'market'}<div class="p-4"><MarketView /></div>
      {:else if route.view === 'plans'}<PlansView id={route.id} />
      {:else if route.view === 'research'}<ResearchView id={route.id} onStartResearch={startResearch} />
      {:else if route.view === 'retrospect'}<RetrospectView />
      {:else if route.view === 'jobs'}<div class="p-4"><JobsView /></div>{/if}
    {/if}
  {/snippet}
  {#snippet drawerPane(pane)}
    {#if pane === 'dash'}<DashboardPane onBring={bring} />
    {:else if pane === 'market'}<MarketPane onBring={bring} />
    {:else if pane === 'jobs'}<JobsPane onBring={bring} />
    {:else}<ToolsPane />{/if}
  {/snippet}
</Shell>
<SettingsSheet bind:open={settingsOpen} onSaved={invalidateAll} />
