<script lang="ts">
  import { onMount } from 'svelte'
  import PageHeader from '$lib/ui/domain/PageHeader.svelte'
  import Icon from '$lib/ui/domain/Icon.svelte'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import { Button } from '$lib/ui/button/index'
  import { get } from '$lib/api'
  import { fmtBeijing } from '$lib/format'
  import TodayActions from '$lib/home/TodayActions.svelte'
  import BriefPanel from '$lib/home/BriefPanel.svelte'
  import PlanCards from '$lib/home/PlanCards.svelte'
  import ResearchCards from '$lib/home/ResearchCards.svelte'
  import RetrospectPanel from '$lib/home/RetrospectPanel.svelte'
  import AccountStrip from '$lib/home/AccountStrip.svelte'
  import ChatPanel from '$lib/home/ChatPanel.svelte'
  import SettingsSheet from '$lib/home/SettingsSheet.svelte'
  import { errorText, type Today } from '$lib/home/types'

  // 唯一的页面（spec §12）：抬头（数据截至、总资产、设置入口）+ 内容块 + 助手面板。
  // 宽屏两栏，每栏各自纵向堆叠（不共用行高）：左栏今日操作、简报、复盘；右栏账户条、计划与持仓、研究。
  // 窄屏两栏的外壳用 contents 摊平成单栏，顺序靠 order-*：今日操作 → 账户条 → 计划与持仓 → 研究 → 简报 → 复盘（spec §10）。
  // 对话在右下角悬浮按钮打开的助手面板里（用户 2026-11-07）：非模态，关闭只隐藏不卸载，一轮回答不会断。
  let today = $state<Today | null>(null)
  let todayError = $state('')
  let plansVersion = $state(0)
  let chatsVersion = $state(0)
  let settingsOpen = $state(false)
  let assistantOpen = $state(false)
  let assistantOpener: HTMLElement | null = null
  let prefill = $state<{ text: string; seq: number } | undefined>(undefined)
  let prefillSeq = 0

  function openAssistant() {
    assistantOpener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    assistantOpen = true
  }

  function closeAssistant() {
    assistantOpen = false
    assistantOpener?.focus()  // 焦点还给打开前的元素（design-system §键盘）
  }

  async function loadToday() {
    todayError = ''
    try {
      today = await get<Today>('/api/today', { errorDetails: true })
    } catch (cause) {
      todayError = errorText(cause)
    }
  }

  onMount(loadToday)

  // 页面里任何一处写入完成：今日操作重取，计划组与研究组、复盘重取（version 变化触发）。
  function reload() {
    loadToday()
    plansVersion += 1
  }

  // 研究在对话里发起（spec §4）：打开助手面板并把输入框预填为一句话
  function startResearch(text: string) {
    prefillSeq += 1
    prefill = { text, seq: prefillSeq }
    openAssistant()
  }
</script>

<div class="grid gap-4 py-4">
  <PageHeader title="投资助手" level={1}>
    {#snippet meta()}
      {#if today}
        <!-- 副标按接口契约：「数据截至 {session}」；没有运行记录（session 为空）时退到采集时刻 -->
        <span>数据截至 {today.session ?? fmtBeijing(today.cutoff_at)}</span>
        <span class="flex items-center gap-1">总资产 <MetricValue value={null} raw={today.account.equity} currency /></span>
      {:else if todayError}
        <span>{todayError}</span>
      {/if}
    {/snippet}
    {#snippet actions()}
      {#if import.meta.env.DEV}
        <Button variant="link" href="#_ui">组件画廊</Button>
      {/if}
      <Button variant="outline" onclick={() => (settingsOpen = true)}>
        <Icon name="settings" />
        <span class="sr-only">设置</span>
      </Button>
    {/snippet}
  </PageHeader>

  <div class="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:items-start">
    <div data-column="main" class="contents lg:flex lg:flex-col lg:gap-4">
      <div class="order-1"><TodayActions actions={today?.actions ?? []} onChanged={reload} /></div>
      <div class="order-5"><BriefPanel /></div>
      <div class="order-6"><RetrospectPanel version={plansVersion} /></div>
    </div>
    <div data-column="side" class="contents lg:flex lg:flex-col lg:gap-4">
      <div class="order-2"><AccountStrip account={today?.account ?? null} onChanged={reload} /></div>
      <div class="order-3"><PlanCards version={plansVersion} onChanged={reload} /></div>
      <div class="order-4"><ResearchCards version={plansVersion} onChanged={reload} onStartResearch={startResearch} /></div>
    </div>
  </div>
</div>

<svelte:window onkeydown={(event) => { if (assistantOpen && event.key === 'Escape') closeAssistant() }} />

{#if !assistantOpen}
  <Button variant="primary" class="fixed right-6 bottom-6 z-30 shadow-2" aria-label="打开助手" onclick={openAssistant}>
    <Icon name="message-square" />
    <span>助手</span>
  </Button>
{/if}

<aside
  aria-label="助手"
  hidden={!assistantOpen}
  class="bg-popover text-popover-foreground shadow-2 fixed inset-y-0 right-0 z-40 w-full overflow-y-auto border-l border-border p-4 sm:max-w-[28rem]"
>
  <div class="mb-2 flex justify-end">
    <Button variant="ghost" size="icon" aria-label="关闭助手" onclick={closeAssistant}>
      <Icon name="x" />
    </Button>
  </div>
  <ChatPanel version={chatsVersion} {prefill} />
</aside>

<SettingsSheet bind:open={settingsOpen} onSaved={() => (chatsVersion += 1)} />
