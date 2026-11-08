<script lang="ts">
  import * as Card from '$lib/ui/card/index'
  import * as Collapsible from '$lib/ui/collapsible/index'
  import { Button } from '$lib/ui/button/index'
  import ConfirmDialog from '$lib/ui/confirm-dialog/ConfirmDialog.svelte'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import { Skeleton } from '$lib/ui/skeleton/index'
  import { get, post } from '$lib/api'
  import { notify } from '$lib/ui/toast'
  import { errorText, type ResearchCardData, type ResearchDetail, type WritePreview } from './types'

  // 一张研究卡片（spec §10 研究块）：股票、状态、第几天、论点、条件与最新收盘、五种显示文案、
  // agent 结论一句、已转计划的关联；展开拉 GET /api/research/{id} 看时间线；观察中的可放弃。
  let { card, onChanged }: { card: ResearchCardData; onChanged?: () => void } = $props()

  const VERDICT_TEXT: Record<string, string> = { ready: '可以出计划', watch: '值得盯', reject: '否决' }

  // 五种显示文案从卡片字段推导（Task 4 契约，与研究段的 state 同序）：触发 → 无条件 → 缺日线
  // → 均线日线不足（close_above_ma 且 condition_met_today 为 null 而当天有日线）→ 观察中。
  // 只对观察中的研究显示；已结束的看状态徽标与结束原因。
  const watching = $derived(card.status === 'watching')
  const stateText = $derived.by(() => {
    if (!watching) return ''
    if (card.triggered_session != null || card.condition_met_today === true) return '条件已到，待复看'
    if (card.condition == null) return '尚无条件'
    if (card.missing_bar) return '缺日线，未复查'
    if (card.condition_met_today == null) return '日线不足，未检查'
    return '观察中'
  })
  const stateWarn = $derived(
    stateText === '条件已到，待复看' || stateText === '缺日线，未复查' || stateText === '日线不足，未检查',
  )

  // ---- 变化记录：首次展开时取 GET /api/research/{id} ----
  let timelineOpen = $state(false)
  let detail = $state<ResearchDetail | null>(null)
  let detailError = $state('')

  $effect(() => {
    if (!timelineOpen || detail || detailError) return
    get<ResearchDetail>(`/api/research/${card.research_id}`)
      .then((row) => (detail = row))
      .catch((cause) => (detailError = errorText(cause)))
  })

  // ---- 放弃研究（观察中；A18 两步确认）----
  let dropOpen = $state(false)
  let dropPreview = $state('')
  let dropWriting = $state(false)
  let dropId = ''

  async function dropResearch() {
    try {
      dropId = crypto.randomUUID()
      const first = await post<WritePreview>(`/api/research/${card.research_id}/drop`, {
        request_id: dropId,
        confirmed: false,
      })
      dropPreview = first.preview ?? ''
      dropOpen = true
    } catch (cause) {
      notify.error(errorText(cause))
    }
  }

  async function writeDrop() {
    dropWriting = true
    try {
      await post(`/api/research/${card.research_id}/drop`, { request_id: dropId, confirmed: true })
      notify.success('已放弃研究')
      dropOpen = false
      onChanged?.()
    } catch (cause) {
      notify.error(errorText(cause))
      dropOpen = false
    } finally {
      dropWriting = false
    }
  }
</script>

<Card.Root size="sm" class={watching ? '' : 'opacity-80'}>
  <Card.Header>
    <Card.Title class="flex flex-wrap items-center gap-2">
      {card.ticker}
      <StatusPill domain="research" value={card.status} />
    </Card.Title>
    <Card.Description>
      研究 {card.research_id} · 第 {card.day} 天 · 有效至 {card.valid_until}
    </Card.Description>
  </Card.Header>
  <Card.Content class="grid gap-2">
    <p class="text-sm">{card.thesis}</p>
    <p class="flex flex-wrap items-center gap-1 text-sm">
      <span class="text-muted-foreground">条件：</span>{card.condition_text ?? '尚无条件'}
      <span class="text-muted-foreground">· 最新收盘</span>
      <MetricValue value={null} raw={card.last_close} currency />
    </p>
    {#if stateText}
      <p class={stateWarn ? 'text-sm text-warn' : 'text-sm text-muted-foreground'}>{stateText}</p>
    {/if}
    {#if card.verdict}
      <p class="text-xs text-muted-foreground">结论：{VERDICT_TEXT[card.verdict] ?? card.verdict}{card.reasons ? `：${card.reasons}` : ''}</p>
    {/if}
    {#if card.plan_id != null}
      <p class="text-xs text-muted-foreground">已转计划 #{card.plan_id}</p>
    {/if}
    {#if !watching && card.end_reason}
      <p class="text-xs text-muted-foreground">结束：{card.end_reason}</p>
    {/if}
  </Card.Content>
  <Card.Footer class="grid gap-2">
    <div class="flex flex-wrap gap-2">
      {#if watching}
        <Button size="sm" variant="outline" onclick={dropResearch}>放弃研究</Button>
      {/if}
    </div>
    <Collapsible.Root bind:open={timelineOpen}>
      <Collapsible.Trigger class="text-xs text-muted-foreground underline-offset-2 hover:underline">变化记录</Collapsible.Trigger>
      {#if timelineOpen}
        <Collapsible.Content forceMount class="grid gap-1 pt-1">
          {#if detailError}
            <p class="text-xs text-muted-foreground">{detailError}</p>
          {:else if detail}
            <ul class="grid gap-1 text-xs text-muted-foreground">
              {#each detail.events as event, index (index)}
                <li>{event.session ?? ''}：{event.reason ?? event.event}</li>
              {/each}
            </ul>
          {:else}
            <Skeleton class="h-4 w-full" />
          {/if}
        </Collapsible.Content>
      {/if}
    </Collapsible.Root>
  </Card.Footer>
</Card.Root>

<ConfirmDialog
  bind:open={dropOpen}
  title="放弃研究"
  consequence={dropPreview}
  actionLabel="放弃研究"
  destructive
  busy={dropWriting}
  onConfirm={writeDrop}
  onCancel={() => (dropPreview = '')}
/>
