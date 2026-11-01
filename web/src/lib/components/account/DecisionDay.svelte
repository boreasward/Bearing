<script lang="ts">
  import type { PaperDecision } from '$lib/accounts'
  import { decisionReasonText } from '$lib/accounts'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import OrdersTable from './OrdersTable.svelte'

  let { decision }: { decision: PaperDecision } = $props()
  const LABELS: Record<string, string> = {
    account: '账户与现金', positions: '持仓', pending_orders: '在挂订单', buyable: '可买计划',
    rejected_yesterday: '昨日被拒订单', recent_closed: '近期平仓', market: '市场环境',
    plans: '候选计划', recent_trades: '近期成交', constraints: '风险约束',
  }
  const raw = (value: unknown) => value == null ? '未提供' : typeof value === 'string' ? value : JSON.stringify(value, null, 2)
</script>

<div class="grid min-w-0 gap-4">
  <div class="flex flex-wrap items-center gap-2 text-sm"><span>{decision.session}</span><StatusPill domain="paper_decision" value={decision.status} /></div>
  {#if decision.status === 'skipped'}<DataBlock state="degraded" message={`跳过原因：${decisionReasonText(decision.status_reason)}`} />
  {:else if decision.status_reason}<DataBlock state="degraded" message={decisionReasonText(decision.status_reason)} />{/if}
  <Section level={3} title="Agent 看到的">
    <DataBlock state={Object.keys(decision.evidence ?? {}).length ? 'ready' : 'not_computed'} message="该日未保存证据包">
      <div class="grid min-w-0 gap-3 md:grid-cols-2">
        {#each Object.entries(decision.evidence ?? {}) as [key, value]}
          <section class="min-w-0 rounded-md border border-border p-3" data-evidence-key={key}>
            <h4 class="mb-1 text-sm font-medium">{LABELS[key] ?? key}</h4>
            <pre class="max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{raw(value)}</pre>
          </section>
        {/each}
      </div>
    </DataBlock>
  </Section>
  <Section level={3} title="Agent 的决定与理由">
    <OrdersTable orders={decision.orders ?? []} />
    {#if decision.output}<details class="text-xs"><summary class="cursor-pointer text-primary">决定原文</summary><pre class="mt-2 whitespace-pre-wrap break-all">{raw(decision.output)}</pre></details>{/if}
  </Section>
  <Section level={3} title="继续持有（holds）">
    <DataBlock state={decision.holds?.length ? 'ready' : 'empty'} message="该日没有继续持有说明">
      <ul class="grid gap-1 text-sm">{#each decision.holds ?? [] as hold}<li class="break-all">{raw(hold)}</li>{/each}</ul>
    </DataBlock>
  </Section>
  <Section level={3} title="总结"><p class="whitespace-pre-wrap break-all text-sm">{raw(decision.summary) ?? '未提供总结'}</p></Section>
</div>
