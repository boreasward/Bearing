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
  import AccountStrip from '$lib/home/AccountStrip.svelte'
  import ChatPanel from '$lib/home/ChatPanel.svelte'
  import SettingsSheet from '$lib/home/SettingsSheet.svelte'
  import { errorText, type Today } from '$lib/home/types'

  // 唯一的页面（spec §12）：抬头（数据截至、总资产、设置入口）+ 四块内容。
  // 宽屏两栏：左栏今日操作、简报、对话；右栏账户条、计划与持仓。窄屏顺序用 order-*：
  // 今日操作 → 账户条 → 计划与持仓 → 简报 → 对话（账户条随计划组走）。
  let today = $state<Today | null>(null)
  let todayError = $state('')
  let plansVersion = $state(0)
  let chatsVersion = $state(0)
  let settingsOpen = $state(false)

  async function loadToday() {
    todayError = ''
    try {
      today = await get<Today>('/api/today')
    } catch (cause) {
      todayError = errorText(cause)
    }
  }

  onMount(loadToday)

  // 页面里任何一处写入完成：今日操作重取，计划组重取（version 变化触发）。
  function reload() {
    loadToday()
    plansVersion += 1
  }
</script>

<div class="grid gap-4 py-4">
  <PageHeader title="投资助手" level={1}>
    {#snippet meta()}
      {#if today}
        <span>数据截至 {fmtBeijing(today.cutoff_at)}</span>
        <span class="flex items-center gap-1">总资产 <MetricValue value={null} raw={today.account.equity} /></span>
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

  <div class="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
    <div class="order-1"><TodayActions actions={today?.actions ?? []} onChanged={reload} /></div>
    <div class="order-2"><AccountStrip account={today?.account ?? null} onChanged={reload} /></div>
    <div class="order-3"><PlanCards version={plansVersion} onChanged={reload} /></div>
    <div class="order-4"><BriefPanel /></div>
    <div class="order-5"><ChatPanel version={chatsVersion} /></div>
  </div>
</div>

<SettingsSheet bind:open={settingsOpen} onSaved={() => (chatsVersion += 1)} />
