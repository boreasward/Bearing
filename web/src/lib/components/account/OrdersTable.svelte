<script lang="ts">
  import type { PaperOrder } from '$lib/accounts'
  import { decimalText, ORDER_STATUS_TEXT, orderReasonText, REJECT_TEXT } from '$lib/accounts'
  import { DataTable } from '$lib/ui/data-table/index'
  import type { Column } from '$lib/ui/data-table/types'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'

  let { orders }: { orders: PaperOrder[] } = $props()
  const actions: Record<string, string> = { buy: '买入', sell: '卖出', sell_half: '卖出一半', hold: '持有', cancel: '撤单', replace: '换单', skip: '跳过' }
  function host(order: PaperOrder): string {
    const code = order.rejection_code ?? order.reject_code ?? order.reject_reason ?? order.status_reason
    if (code && (REJECT_TEXT as Record<string, string>)[code]) return (REJECT_TEXT as Record<string, string>)[code]
    if (order.host_check) return typeof order.host_check === 'string' ? order.host_check : JSON.stringify(order.host_check)
    return code ?? '—'
  }
  function result(order: PaperOrder): string {
    if (order.result_text) return order.result_text
    if (order.filled_price != null) return `成交 ${order.filled_price}`
    const reason = orderReasonText(order.status, order.status_reason)
    return reason ? `${ORDER_STATUS_TEXT[order.status]}：${reason}` : '—'
  }
  const columns: Column<PaperOrder>[] = [
    { key: 'action', header: '动作', value: o => o.action === 'sell' && o.portion === 'half' ? '卖出一半' : actions[o.action ?? o.side ?? ''] ?? o.action ?? o.side ?? '—' },
    { key: 'ticker', header: '标的' }, { key: 'shares', header: '股数', align: 'num', value: o => decimalText(o.status === 'filled' ? o.filled_quantity ?? o.shares ?? o.quantity : o.shares ?? o.quantity) },
    { key: 'plan', header: '依据计划', cell: plan }, { key: 'reason', header: '理由原文', value: o => o.reason ?? '—' },
    { key: 'host', header: '宿主检查', value: host }, { key: 'status', header: '状态', cell: status },
    { key: 'result', header: '结果 / 次日回填', value: result },
  ]
</script>
{#snippet plan(order: PaperOrder)}{#if order.plan_id}<a class="text-primary underline" href={`#plan/${order.plan_id}`}>{order.plan_label ?? order.plan_id}</a>{:else}—{/if}{/snippet}
{#snippet status(order: PaperOrder)}<StatusPill domain="paper_order" value={order.status} />{/snippet}
<DataBlock state={orders.length ? 'ready' : 'empty'} message="暂无模拟订单">
  <DataTable {columns} rows={orders} rowKey={o => o.id} ariaLabel="模拟订单" cards="narrow" />
</DataBlock>
