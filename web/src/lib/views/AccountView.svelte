<script lang="ts">
  import { account, ledgerForLimit, invalidateAll } from '$lib/stores'
  import PageHeader from '$lib/ui/domain/PageHeader.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import DataTable from '$lib/ui/data-table/DataTable.svelte'
  import { Button } from '$lib/ui/button/index'
  import TradeForm from '$lib/home/TradeForm.svelte'
  import ResourceBlock from './ResourceBlock.svelte'
  import AccountCard from './cards/AccountCard.svelte'
  import { actionCompleted, bringToChat } from './context'
  import { fmtBeijing } from '$lib/format'
  import type { CashFlowRow, EquityRow, LedgerData } from '$lib/stores/types'
  type Trade = LedgerData['trades'][number]
  let limit = $state(20)
  const ledger = $derived(ledgerForLimit(limit))
  const more = $derived(limit < 100 && ledger.data && [ledger.data.trades, ledger.data.cash_flows, ledger.data.equity].some(rows => rows.length >= limit))
  let tradeOpen = $state(false)
  const flowKinds: Record<string, string> = { opening:'期初', deposit:'入金', withdrawal:'出金', adjustment:'调整' }
</script>
{#snippet tradeAmount(row: Trade)}<MetricValue value={null} raw={row.price} currency />{/snippet}
{#snippet fee(row: Trade)}<MetricValue value={null} raw={row.fee} currency />{/snippet}
{#snippet tradeTicker(row: Trade)}<span>{row.ticker}</span><Button size="sm" variant="ghost" onclick={() => bringToChat({ kind:row.plan_id == null ? 'ticker' : 'plan', ticker:row.ticker, id:row.plan_id })}>带进对话</Button>{/snippet}
{#snippet flowAmount(row: CashFlowRow)}<MetricValue value={null} raw={row.amount} currency />{/snippet}
{#snippet equityAmount(row: EquityRow)}<MetricValue value={null} raw={row.equity} currency />{/snippet}
<div class="grid gap-5">
  <PageHeader title="账户" level={1}>{#snippet meta()}金额为美元 · 页面时间为北京{/snippet}</PageHeader>
  <ResourceBlock resource={account} emptyMessage="还没有账户记录">
    {#if account.data}<AccountCard data={account.data} />{/if}
  </ResourceBlock>
  <div><Button variant="outline" onclick={() => tradeOpen = true}>记成交</Button></div>
  <ResourceBlock resource={ledger} emptyMessage="还没有流水记录">
    <Section title="成交">
      <DataTable ariaLabel="成交流水"
        columns={[
          { key:'ticker', header:'股票', cell:tradeTicker },
          { key:'side', header:'方向', value:row => row.side === 'buy' ? '买入' : '卖出' },
          { key:'quantity', header:'股数', align:'num' },
          { key:'price', header:'价格', align:'num', cell:tradeAmount },
          { key:'fee', header:'手续费', align:'num', cell:fee },
          { key:'executed_at', header:'成交时间', value:row => fmtBeijing(row.executed_at) },
          { key:'void_reason', header:'作废原因', value:row => row.voided_by == null ? '有效' : `已作废：${row.void_reason ?? '未记录原因'}` }
        ]}
       rows={ledger.data?.trades ?? []} rowKey={row => row.id} muted={row => row.voided_by != null} pageSize={0} />
      {#if !ledger.data?.trades.length}<p class="text-sm text-muted-foreground">没有成交流水</p>{/if}
    </Section>
    <Section title="出入金">
      <DataTable ariaLabel="出入金流水"
        columns={[
          { key:'kind', header:'类型', value:row => flowKinds[row.kind] ?? row.kind },
          { key:'amount', header:'金额', align:'num', cell:flowAmount },
          { key:'effective_at', header:'生效时间', value:row => fmtBeijing(row.effective_at) },
          { key:'note', header:'备注' },
          { key:'void_reason', header:'作废原因', value:row => row.voided_by == null ? '有效' : `已作废：${row.void_reason ?? '未记录原因'}` }
        ]}
       rows={ledger.data?.cash_flows ?? []} rowKey={row => row.id} muted={row => row.voided_by != null} pageSize={0} />
      {#if !ledger.data?.cash_flows.length}<p class="text-sm text-muted-foreground">没有出入金流水</p>{/if}
    </Section>
    <Section title="对账">
      <DataTable ariaLabel="对账流水"
        columns={[
          { key:'as_of', header:'截至日期' },
          { key:'equity', header:'券商总资产', align:'num', cell:equityAmount },
          { key:'note', header:'备注' }
        ]}
       rows={ledger.data?.equity ?? []} rowKey={row => row.id} pageSize={0} />
      {#if !ledger.data?.equity.length}<p class="text-sm text-muted-foreground">没有对账流水</p>{/if}
    </Section>
    {#if more}<div><Button size="sm" onclick={() => limit += 20}>显示更多</Button></div>{/if}
  </ResourceBlock>
</div>
<TradeForm bind:open={tradeOpen} onDone={() => { invalidateAll(); actionCompleted('已记录成交') }} />
