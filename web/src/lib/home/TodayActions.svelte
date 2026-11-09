<script lang="ts">
  import { bringToChat } from '$lib/views/context'
  import Section from '$lib/ui/domain/Section.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import { Button } from '$lib/ui/button/index'
  import ConfirmDialog from '$lib/ui/confirm-dialog/ConfirmDialog.svelte'
  import { post } from '$lib/api'
  import { notify } from '$lib/ui/toast'
  import TradeForm from './TradeForm.svelte'
  import { errorText, type TodayAction, type WritePreview } from './types'

  // 今日操作（spec §12）：置顶块，没有内容时整块不出现。每条一行带直接可点的动作：
  // 「确认已挂单」两步确认，「记成交」打开预填表单。
  let { actions, onChanged }: { actions: TodayAction[]; onChanged?: () => void } = $props()

  // spec §7.4 的规则次序：止损触及未卖出最紧急，其后依次卖出指令、缺日线、止损单、持有
  const ORDER: Record<string, number> = { stop_hit_unresolved: 0, sell_next_open: 1, unchecked: 2, stop_order: 3, hold: 4 }
  const sorted = $derived([...actions].sort((a, b) => (ORDER[a.kind] ?? 9) - (ORDER[b.kind] ?? 9)))

  let busy = $state(false)
  let confirmOpen = $state(false)
  let pending = $state<{ preview: string; write: () => Promise<unknown> } | null>(null)
  let writing = $state(false)

  // 「确认已挂单」（A18）：先 confirmed=false 取 preview，对话框确认后 confirmed=true
  async function confirmPlaced(action: TodayAction) {
    if (busy) return
    busy = true
    try {
      const requestId = crypto.randomUUID()
      const send = (confirmed: boolean) =>
        post<unknown>(`/api/plans/${action.plan_id}/stop-order`, {
          price: Number(action.stop),
          request_id: requestId,
          confirmed,
        })
      const first = await post<WritePreview>(`/api/plans/${action.plan_id}/stop-order`, {
        price: Number(action.stop),
        request_id: requestId,
        confirmed: false,
      })
      pending = { preview: first.preview ?? '', write: () => send(true) }
      confirmOpen = true
    } catch (cause) {
      notify.error(errorText(cause))
    } finally {
      busy = false
    }
  }

  async function writeStopOrder() {
    if (!pending) return
    writing = true
    try {
      await pending.write()
      notify.success('已确认挂单')
      confirmOpen = false
      pending = null
      onChanged?.()
    } catch (cause) {
      notify.error(errorText(cause))
      confirmOpen = false
      pending = null
    } finally {
      writing = false
    }
  }

  let tradeOpen = $state(false)
  let tradeTarget = $state<TodayAction | null>(null)
</script>

{#if sorted.length}
  <Section title="今日操作" level={2}>
    <ul class="grid gap-2">
      {#each sorted as action (action.plan_id)}
        <li class="grid gap-1.5 rounded-md border border-border bg-card p-3">
          <div class="flex flex-wrap items-center gap-2">
            <span class="text-sm font-medium">{action.ticker}</span>
            <StatusPill domain="plan_action" value={action.kind} text={action.text} />
          </div>
          {#if action.stop || action.quantity}
            <p class="text-xs text-muted-foreground">
              {#if action.stop}止损 {action.stop} · {/if}{#if action.quantity}持仓 {action.quantity} 股{/if}
            </p>
          {/if}
          {#if action.reason}<p class="text-xs text-muted-foreground">{action.reason}</p>{/if}
          <div class="flex flex-wrap gap-2">
            <Button size="sm" variant="ghost" onclick={() => bringToChat({ kind:'plan', ticker:action.ticker, id:action.plan_id })}>带进对话</Button>
            {#if action.kind === 'stop_order' && action.stop != null}
              <Button size="sm" onclick={() => confirmPlaced(action)}>确认已挂单</Button>
            {/if}
            {#if action.kind === 'stop_hit_unresolved' || action.kind === 'sell_next_open'}
              <Button
                size="sm"
                variant="outline"
                onclick={() => {
                  tradeTarget = action
                  tradeOpen = true
                }}>记成交</Button
              >
            {/if}
          </div>
        </li>
      {/each}
    </ul>
  </Section>
{:else}
  <!-- 没有操作时也留一张小卡片（用户 2026-11-07），左栏顶部不再空出一块 -->
  <Section title="今日操作" level={2}>
    <p class="rounded-md border border-border bg-card px-3 py-2 text-sm text-muted-foreground">今天没有需要操作的事项</p>
  </Section>
{/if}

<ConfirmDialog
  bind:open={confirmOpen}
  title="确认已挂单"
  consequence={pending?.preview ?? ''}
  actionLabel="确认已挂单"
  busy={writing}
  onConfirm={writeStopOrder}
  onCancel={() => (pending = null)}
/>

<TradeForm
  bind:open={tradeOpen}
  ticker={tradeTarget?.ticker ?? ''}
  planId={tradeTarget?.plan_id ?? null}
  shares={tradeTarget?.quantity ?? null}
  onDone={onChanged}
/>
