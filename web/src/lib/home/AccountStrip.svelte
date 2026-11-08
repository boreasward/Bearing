<script lang="ts">
  import * as Card from '$lib/ui/card/index'
  import { Alert, AlertDescription } from '$lib/ui/alert/index'
  import { Button } from '$lib/ui/button/index'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import CashFlowForm from './CashFlowForm.svelte'
  import ReconcileForm from './ReconcileForm.svelte'
  import type { AccountRow } from './types'

  // 账户条（spec §12）：现金、总资产、浮动盈亏、已实现盈亏；「记出入金」「对账」两个入口。
  // account 为空（页面还在取 /api/today）时显示骨架。
  let { account = null, onChanged }: { account?: AccountRow | null; onChanged?: () => void } = $props()

  let cashOpen = $state(false)
  let reconcileOpen = $state(false)
</script>

<Card.Root size="sm">
  <Card.Header><Card.Title>账户</Card.Title></Card.Header>
  <Card.Content class="grid gap-2">
    <DataBlock state={account ? 'ready' : 'loading'} skeleton="row">
      {#if account}
        <dl class="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
          {#each [{ label: '现金', value: account.cash, tone: 'none' as const }, { label: '总资产', value: account.equity, tone: 'none' as const }, { label: '浮动盈亏', value: account.unrealized_pnl, tone: 'updown' as const }, { label: '已实现盈亏', value: account.realized_pnl, tone: 'updown' as const }] as metric (metric.label)}
            <div class="grid gap-0.5">
              <dt class="text-xs text-muted-foreground">{metric.label}</dt>
              <dd><MetricValue value={null} raw={metric.value} currency tone={metric.tone} /></dd>
            </div>
          {/each}
        </dl>
        {#if account.cash_negative || account.notes.length}
          <Alert variant="warn"><AlertDescription>{account.notes.join('；') || '现金为负'}</AlertDescription></Alert>
        {/if}
      {/if}
    </DataBlock>
  </Card.Content>
  <Card.Footer class="flex flex-wrap gap-2">
    <Button size="sm" variant="outline" onclick={() => (cashOpen = true)}>记出入金</Button>
    <Button size="sm" variant="outline" onclick={() => (reconcileOpen = true)}>对账</Button>
  </Card.Footer>
</Card.Root>

<CashFlowForm bind:open={cashOpen} onDone={onChanged} />
<ReconcileForm bind:open={reconcileOpen} onDone={onChanged} />
