<script lang="ts">
  import { today, plans, research, market, invalidateAll } from '$lib/stores'
  import Section from '$lib/ui/domain/Section.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import PlanLevels from '$lib/ui/domain/PlanLevels.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import { Button } from '$lib/ui/button/index'
  import ConfirmDialog from '$lib/ui/confirm-dialog/ConfirmDialog.svelte'
  import TradeForm from '$lib/home/TradeForm.svelte'
  import { sortActions } from '$lib/home/action-order'
  import { post } from '$lib/api'
  import { notify } from '$lib/ui/toast'
  import { errorText, type WritePreview } from '$lib/home/types'
  import { actionCompleted, type ContextChip } from '$lib/views/context'
  import { num } from '$lib/views/data'
  import ResourceBlock from '$lib/views/ResourceBlock.svelte'
  import type { TodayData } from '$lib/stores/types'
  let { onBring }: { onBring(chip: ContextChip): void } = $props()
  const holdings = plans('holdings')
  const pending = plans('pending')
  const watching = research('watching')
  const planRows = $derived([...(holdings.data?.plans ?? []), ...(pending.data?.plans ?? [])])
  type Action = TodayData['actions'][number]
  const todayActions = $derived(sortActions(today.data?.actions ?? []))
  let tradeTarget = $state<Action | null>(null)
  type StopRequest = Readonly<{ planId: number; price: number | null; requestId: string }>
  let stopRequest = $state<StopRequest | null>(null)
  let tradeOpen = $state(false)
  let confirmOpen = $state(false)
  let preview = $state('')
  let busy = $state(false)
  let writing = $state(false)
  function completed(message: string) { invalidateAll(); actionCompleted(message) }
  async function confirmPlaced(action: Action) {
    if (busy || confirmOpen || writing) return
    busy = true
    const request: StopRequest = { planId: action.plan_id, price: num(action.stop), requestId: crypto.randomUUID() }
    try {
      const first = await post<WritePreview>(`/api/plans/${request.planId}/stop-order`, { price:request.price, request_id:request.requestId, confirmed:false })
      stopRequest = request
      preview = first.preview ?? ''; confirmOpen = true
    } catch (cause) { notify.error(errorText(cause)) } finally { busy = false }
  }
  async function writeStop() {
    const request = stopRequest
    if (!request || writing) return
    writing = true
    try {
      await post(`/api/plans/${request.planId}/stop-order`, { price:request.price, request_id:request.requestId, confirmed:true })
      confirmOpen = false; notify.success('已确认挂单'); completed('已确认挂单')
    } catch (cause) { notify.error(errorText(cause)); confirmOpen = false } finally { writing = false; stopRequest = null }
  }
</script>
<div class="grid gap-5 text-sm">
  <Section title={`今日操作（${todayActions.length}）`}>
    {#snippet actions()}<a href="#today" aria-label="打开今日">›</a>{/snippet}
    <ResourceBlock resource={today} emptyMessage="今日无操作">
      <DataBlock state={todayActions.length ? 'ready' : 'empty'} message="今日无操作">
        <ul class="grid gap-3">{#each todayActions as action (action.plan_id)}
          <li class="grid gap-1"><span>{action.ticker} · {action.text}</span>
            <div class="flex flex-wrap gap-1">
              {#if action.kind === 'stop_order' && action.stop != null}<Button size="sm" variant="primary" disabled={busy} onclick={() => confirmPlaced(action)}>确认已挂单</Button>{/if}
              {#if action.kind === 'stop_hit_unresolved' || action.kind === 'sell_next_open' || action.kind === 'rotation_sell'}<Button size="sm" variant="primary" onclick={() => { tradeTarget = action; tradeOpen = true }}>记成交</Button>{/if}
              <Button size="sm" variant="ghost" onclick={() => onBring({ kind:'plan', ticker:action.ticker, id:action.plan_id })}>带进对话</Button>
            </div>
          </li>
        {/each}</ul>
      </DataBlock>
    </ResourceBlock>
  </Section>
  <Section title="账户">
    {#snippet actions()}<a href="#account" aria-label="打开账户">›</a>{/snippet}
    <ResourceBlock resource={today} emptyMessage="还没有账户记录">
      <p class="text-xl"><MetricValue value={num(today.data?.account.equity)} currency /></p>
      <p>现金 <MetricValue value={num(today.data?.account.cash)} currency /></p>
      <p>浮动盈亏 <MetricValue value={num(today.data?.account.unrealized_pnl)} currency tone="updown" /></p>
    </ResourceBlock>
  </Section>
  <Section title="持仓与计划">
    {#snippet actions()}<a href="#plans" aria-label="打开计划与持仓">›</a>{/snippet}
    <ResourceBlock resource={holdings} emptyMessage="无持仓与计划">
      <ResourceBlock resource={pending} emptyMessage="无持仓与计划">
        <DataBlock state={planRows.length ? 'ready' : 'empty'} message="无持仓与计划">
          <ul class="grid gap-3">{#each planRows as plan (plan.plan_id)}
            <li class="grid gap-1"><span>{plan.ticker} <StatusPill domain="plan" value={plan.status} /></span>
              <PlanLevels low={num(plan.levels.entry_low)} high={num(plan.levels.entry_high)} stop={num(plan.levels.stop)} target1={num(plan.levels.target1)} shares={plan.shares} />
              {#if plan.position}<p>持仓 {plan.position.quantity} 股 · 成本 <MetricValue value={num(plan.position.avg_cost)} currency /></p>{/if}
              <Button size="sm" variant="ghost" onclick={() => onBring({ kind:'plan', ticker:plan.ticker, id:plan.plan_id })}>带进对话</Button>
            </li>
          {/each}</ul>
        </DataBlock>
      </ResourceBlock>
    </ResourceBlock>
  </Section>
  <Section title="研究中">
    {#snippet actions()}<a href="#research" aria-label="打开研究">›</a>{/snippet}
    <ResourceBlock resource={watching} emptyMessage="没有观察中的研究">
      <DataBlock state={watching.data?.research.length ? 'ready' : 'empty'} message="没有观察中的研究">
        <ul class="grid gap-3">{#each watching.data?.research ?? [] as row (row.research_id)}
          <li class="grid gap-1"><p>{row.ticker} · 第 {row.day} 天</p><p class="text-xs text-muted-foreground">{row.condition_text ?? '尚无条件'}</p><Button size="sm" variant="ghost" onclick={() => onBring({ kind:'research', ticker:row.ticker, id:row.research_id })}>带进对话</Button></li>
        {/each}</ul>
      </DataBlock>
    </ResourceBlock>
  </Section>
  <Section title="市场温度">
    {#snippet actions()}<a href="#market" aria-label="打开行情">›</a>{/snippet}
    <ResourceBlock resource={market} emptyMessage="还没有运行记录">
      {#if market.data}<StatusPill domain="regime" value={market.data.label} /><p>广度 <MetricValue value={market.data.breadth.pct_50} percent /></p><p>SPY 20 日 <MetricValue value={market.data.spy_ret20} percent /></p>{/if}
    </ResourceBlock>
  </Section>
</div>
<TradeForm bind:open={tradeOpen} ticker={tradeTarget?.ticker ?? ''} planId={tradeTarget?.plan_id ?? null} shares={tradeTarget?.kind === 'rotation_sell' ? (tradeTarget.sell_quantity ?? num(tradeTarget.quantity)) : num(tradeTarget?.quantity)} side={tradeTarget?.kind === 'rotation_sell' ? 'sell' : 'buy'} onDone={() => completed('已记录成交')} />
<ConfirmDialog bind:open={confirmOpen} title="确认已挂单" consequence={preview} actionLabel="确认已挂单" busy={writing} onConfirm={writeStop} onCancel={() => { preview = ''; stopRequest = null }} />
