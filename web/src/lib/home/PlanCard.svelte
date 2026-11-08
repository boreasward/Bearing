<script lang="ts">
  import * as Card from '$lib/ui/card/index'
  import * as Collapsible from '$lib/ui/collapsible/index'
  import * as Dialog from '$lib/ui/dialog/index'
  import { Alert, AlertDescription } from '$lib/ui/alert/index'
  import { Button } from '$lib/ui/button/index'
  import ConfirmDialog from '$lib/ui/confirm-dialog/ConfirmDialog.svelte'
  import Field from '$lib/ui/field/Field.svelte'
  import Input from '$lib/ui/input/input.svelte'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import PlanLevels from '$lib/ui/domain/PlanLevels.svelte'
  import { Skeleton } from '$lib/ui/skeleton/index'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import { get, post } from '$lib/api'
  import { notify } from '$lib/ui/toast'
  import TradeForm from './TradeForm.svelte'
  import { errorText, type PlanCardData, type PlanDetail, type WritePreview } from './types'

  // 一张计划卡片（spec §12 计划与持仓块）：股票、状态、价位、股数、下一个交易日的操作；
  // 持仓另有浮动盈亏、已持有天数、止损单状态；已结束显示 §8.4 的平仓结果；可展开变化记录。
  let { card, onChanged }: { card: PlanCardData; onChanged?: () => void } = $props()

  const KIND_TEXT: Record<string, string> = { planned: '计划', adopted: '补录' }
  const EXIT_TEXT: Record<string, string> = {
    trailing_stop: '移动止损',
    initial_stop: '初始止损',
    max_hold: '持有到期',
    thesis_broken: '论点失效',
    manual: '手动',
  }
  const ENDED = new Set(['closed', 'expired', 'invalidated', 'replaced', 'cancelled'])
  const ended = $derived(ENDED.has(card.status))

  // 接口价位是字符串：转成数字交给 PlanLevels（design-system §6.2 的约定），转不了的按缺失
  function num(value: string | number | null | undefined): number | null {
    if (value === null || value === undefined || value === '') return null
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }

  // ---- 变化记录：首次展开时取 GET /api/plans/{id} ----
  let timelineOpen = $state(false)
  let detail = $state<PlanDetail | null>(null)
  let detailError = $state('')

  $effect(() => {
    if (!timelineOpen || detail || detailError) return
    get<PlanDetail>(`/api/plans/${card.plan_id}`)
      .then((row) => (detail = row))
      .catch((cause) => (detailError = errorText(cause)))
  })

  // ---- 记成交 ----
  let tradeOpen = $state(false)

  // ---- 取消计划（pending；A18 两步确认）----
  let cancelOpen = $state(false)
  let cancelPreview = $state('')
  let cancelWriting = $state(false)
  let cancelId = ''

  async function cancelPlan() {
    try {
      cancelId = crypto.randomUUID()
      const first = await post<WritePreview>(`/api/plans/${card.plan_id}/cancel`, {
        request_id: cancelId,
        confirmed: false,
      })
      cancelPreview = first.preview ?? ''
      cancelOpen = true
    } catch (cause) {
      notify.error(errorText(cause))
    }
  }

  async function writeCancel() {
    cancelWriting = true
    try {
      await post(`/api/plans/${card.plan_id}/cancel`, { request_id: cancelId, confirmed: true })
      notify.success('已取消计划')
      cancelOpen = false
      onChanged?.()
    } catch (cause) {
      notify.error(errorText(cause))
      cancelOpen = false
    } finally {
      cancelWriting = false
    }
  }

  // ---- 调高止损（open；输入新价位后两步确认）----
  let raiseOpen = $state(false)
  let raiseStop = $state('')
  let raiseError = $state('')
  let raiseConfirmOpen = $state(false)
  let raisePreview = $state('')
  let raiseWriting = $state(false)
  let raiseId = ''

  $effect(() => {
    if (raiseOpen) {
      raiseStop = ''
      raiseError = ''
    }
  })

  async function submitRaise(event: SubmitEvent) {
    event.preventDefault()
    const stop = Number(raiseStop)
    if (!(stop > 0) || raiseWriting) return
    raiseError = ''
    try {
      raiseId = crypto.randomUUID()
      const first = await post<WritePreview>(`/api/plans/${card.plan_id}/raise-stop`, {
        stop,
        request_id: raiseId,
        confirmed: false,
      })
      raisePreview = first.preview ?? ''
      raiseConfirmOpen = true
    } catch (cause) {
      raiseError = errorText(cause)
    }
  }

  async function writeRaise() {
    raiseWriting = true
    try {
      await post(`/api/plans/${card.plan_id}/raise-stop`, {
        stop: Number(raiseStop),
        request_id: raiseId,
        confirmed: true,
      })
      notify.success('已上调止损')
      raiseConfirmOpen = false
      raiseOpen = false
      onChanged?.()
    } catch (cause) {
      notify.error(errorText(cause))
      raiseConfirmOpen = false
    } finally {
      raiseWriting = false
    }
  }
</script>

<Card.Root size="sm" class={ended ? 'opacity-80' : ''}>
  <Card.Header>
    <Card.Title class="flex flex-wrap items-center gap-2">
      {card.ticker}
      <StatusPill domain="plan" value={card.status} />
      {#if KIND_TEXT[card.kind]}<span class="text-xs font-normal text-muted-foreground">{KIND_TEXT[card.kind]}</span>{/if}
    </Card.Title>
    <Card.Description>
      计划 {card.plan_id}
      {#if card.valid_from} · 建立 {card.valid_from}{/if}
      {#if card.trailing} · 移动止损{/if}
    </Card.Description>
  </Card.Header>
  <Card.Content class="grid gap-2">
    <PlanLevels
      low={num(card.levels.entry_low)}
      high={num(card.levels.entry_high)}
      stop={num(card.levels.stop)}
      target1={num(card.levels.target1)}
      shares={num(card.shares)}
    />

    {#if card.status === 'pending'}
      {#if card.valid_until}<p class="text-xs text-muted-foreground">有效至 {card.valid_until}</p>{/if}
      {#if card.close_above_entry}<p class="text-sm text-warn">最新收盘高于入场区间</p>{/if}
      {#if card.missing_bar}<p class="text-sm text-warn">缺日线，未复查</p>{/if}
      {#if card.thesis}<p class="text-xs text-muted-foreground">{card.thesis}</p>{/if}
    {:else if card.status === 'open' && card.position}
      <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        <span class="flex items-center gap-1">浮动盈亏 <MetricValue value={null} raw={card.position.unrealized_pnl} currency tone="updown" /></span>
        <span class="flex items-center gap-1">持仓 <MetricValue value={null} raw={String(card.position.quantity)} unit="股" /></span>
        <span class="flex items-center gap-1">成本 <MetricValue value={null} raw={card.position.avg_cost} currency /></span>
      </div>
      <p class="text-xs text-muted-foreground">
        {#if card.held_sessions != null}已持有 {card.held_sessions} 个交易日 · {/if}
        止损单 <StatusPill domain="stop_order" value={card.stop_order_status} />
      </p>
      {#if card.next_action}
        <div class="flex flex-wrap items-center gap-2 text-sm">
          <span class="text-muted-foreground">下一个交易日</span>
          <StatusPill domain="plan_action" value={card.next_action.kind} text={card.next_action.text} />
        </div>
      {/if}
    {:else if ended && card.outcome && Object.keys(card.outcome).length}
      <ul class="grid gap-1 text-sm">
        <li class="flex items-center gap-1">已实现盈亏 <MetricValue value={null} raw={card.outcome.realized_pnl ?? null} currency tone="updown" /></li>
        {#if card.outcome.r_text}
          <li>{card.outcome.r_text}</li>
        {:else if card.outcome.r_multiple}
          <li>相当于计划风险 {card.outcome.r_multiple} 倍</li>
        {/if}
        {#if card.outcome.exit_reason && EXIT_TEXT[card.outcome.exit_reason]}
          <li>离场原因：{EXIT_TEXT[card.outcome.exit_reason]}</li>
        {/if}
        {#if card.outcome.stop_delay_sessions != null && card.outcome.stop_delay_sessions > 0}
          <li>止损拖延 {card.outcome.stop_delay_sessions} 个交易日</li>
        {/if}
      </ul>
    {/if}
  </Card.Content>
  <Card.Footer class="grid gap-2">
    <div class="flex flex-wrap gap-2">
      {#if card.status === 'pending' || card.status === 'open'}
        <Button size="sm" variant="outline" onclick={() => (tradeOpen = true)}>记成交</Button>
      {/if}
      {#if card.status === 'open'}
        <Button size="sm" variant="outline" onclick={() => (raiseOpen = true)}>调高止损</Button>
      {/if}
      {#if card.status === 'pending'}
        <Button size="sm" variant="outline" onclick={cancelPlan}>取消计划</Button>
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

<TradeForm
  bind:open={tradeOpen}
  ticker={card.ticker}
  planId={card.plan_id}
  shares={card.shares}
  onDone={onChanged}
/>

<ConfirmDialog
  bind:open={cancelOpen}
  title="取消计划"
  consequence={cancelPreview}
  actionLabel="取消计划"
  destructive
  busy={cancelWriting}
  onConfirm={writeCancel}
  onCancel={() => (cancelPreview = '')}
/>

<Dialog.Root bind:open={raiseOpen}>
  <Dialog.Content>
    <Dialog.Header>
      <Dialog.Title>调高止损</Dialog.Title>
      <Dialog.Description>止损只上调不下调，且需低于最新收盘。</Dialog.Description>
    </Dialog.Header>
    <form class="grid gap-3" onsubmit={submitRaise}>
      <Field label="新止损价" id={`raise-${card.plan_id}`} required>
        {#snippet children({ id })}
          <Input {id} inputmode="decimal" class="text-right tabular-nums" bind:value={raiseStop} />
        {/snippet}
      </Field>
      {#if raiseError}
        <Alert variant="danger"><AlertDescription>{raiseError}</AlertDescription></Alert>
      {/if}
      <Dialog.Footer>
        <Button type="submit" variant="primary" disabled={!(Number(raiseStop) > 0)}>提交</Button>
      </Dialog.Footer>
    </form>
  </Dialog.Content>
</Dialog.Root>

<ConfirmDialog
  bind:open={raiseConfirmOpen}
  title="确认调高止损"
  consequence={raisePreview}
  actionLabel="写入"
  busy={raiseWriting}
  onConfirm={writeRaise}
  onCancel={() => (raisePreview = '')}
/>
