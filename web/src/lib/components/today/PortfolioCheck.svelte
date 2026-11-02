<script lang="ts">
  import { PORTFOLIO_REASON_TEXT, type PortfolioCheckView, type PortfolioVerdict } from '$lib/workbench'
  import Section from '$lib/ui/domain/Section.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import CutoffTime from '$lib/ui/domain/CutoffTime.svelte'
  import CalcVersion from '$lib/ui/domain/CalcVersion.svelte'
  import DataTable from '$lib/ui/data-table/DataTable.svelte'
  import type { Column } from '$lib/ui/data-table/types'
  let { check }: { check?: PortfolioCheckView | null } = $props()
  const result = $derived(check?.result)
  const state = $derived(!check || check.status === 'not_computed' || !result ? 'not_computed' : check.status)
  const message = $derived(state === 'degraded' ? `${check?.reason || 'Agent 不可用'}；只有硬结果` : check?.reason || '组合检查节点尚未运行')
  const rows = $derived(result ? [...result.verdicts].sort((a, b) => (a.order ?? Infinity) - (b.order ?? Infinity)) : [])
  const groupName = (group: string | null) => !group || group === 'None' || group === 'null' ? '未归类' : group
  const columns: Column<PortfolioVerdict>[] = [
    { key: 'ticker', header: '标的', cell: ticker }, { key: 'group', header: '行业组', value: r => groupName(r.group) },
    { key: 'verdict', header: '判定', cell: verdict }, { key: 'reasons', header: '原因', value: r => r.reasons.map(k => PORTFOLIO_REASON_TEXT[k] ?? k).join('、') || '通过宿主检查' },
    { key: 'order', header: '宿主先后', align: 'num', value: r => r.order ?? '未排入' },
  ]
  const HOLD_TEXT = { keep: '维持', trim: '减仓', exit: '退出' }
</script>
{#snippet ticker(row: PortfolioVerdict)}<a class="text-primary underline" href={`#plan/${row.plan_id}`}>{row.ticker}</a>{/snippet}
{#snippet verdict(row: PortfolioVerdict)}<StatusPill domain="portfolio_verdict" value={row.verdict} />{/snippet}
<Section title="组合检查" level={2}>
  <DataBlock {state} {message}>
    {#if result}
      <div class="flex flex-wrap gap-3 text-xs"><span>交易日 {check?.session}</span><CutoffTime at={check?.cutoff_at} /><CalcVersion version={result.version} /></div>
      {#if state === 'ready' && check?.commentary}
        <p>{check.commentary.summary}</p>
        <details><summary class="text-sm cursor-pointer">组合经理取舍与建议</summary>
          <ol>{#each [...check.commentary.ranking].sort((a, b) => a.rank - b.rank) as rank}<li>{rank.rank}. <a class="text-primary underline" href={`#plan/${rank.plan_id}`}>{result.verdicts.find(v => v.plan_id === rank.plan_id)?.ticker ?? rank.plan_id}</a>：{rank.reason}</li>{/each}</ol>
          <ul>{#each check.commentary.tradeoffs as tradeoff}<li>{tradeoff.text} <span class="text-xs text-muted-foreground">{tradeoff.refs.join('、')}</span></li>{/each}</ul>
          <ul>{#each check.commentary.hold_actions as action}<li>{action.ticker} · {HOLD_TEXT[action.suggestion]}：{action.reason}</li>{/each}</ul>
        </details>
      {:else if state === 'ready'}<p>Agent 不可用，只有硬结果</p>{/if}
      <DataBlock state={rows.length ? 'ready' : 'empty'} message="本次没有需要检查的计划"><DataTable {columns} {rows} rowKey={r => r.plan_id} ariaLabel="组合计划判定" /></DataBlock>
      <Section title="行业敞口" level={3}>
        {#each Object.entries(result.exposure_by_sector) as [group, exposure]}
          {@const share = exposure.share_of_equity == null ? null : Number(exposure.share_of_equity)}
          <div class="grid gap-1"><p class="text-sm">{groupName(group)} · {share == null ? '权益不可用，占比未计算' : `${(share * 100).toFixed(1)}%`}</p>
            {#if share != null}<meter min="0" max={Math.max(1, share)} value={share} aria-label={`${groupName(group)} 行业敞口`} class="h-3 w-full accent-primary">{exposure.share_of_equity}</meter>{/if}
            <p class="text-xs text-muted-foreground">持仓 {exposure.held_value} · 挂单 {exposure.pending_value} · 候选 {exposure.candidate_value}</p>
          </div>
        {:else}<DataBlock state="empty" message="没有行业敞口" />{/each}
      </Section>
      <Section title="互斥组" level={3}>
        {#each result.conflicts as conflict}<p>{groupName(conflict.group)} · {PORTFOLIO_REASON_TEXT[conflict.rule]}：{#each conflict.plan_ids as id, i}{#if i}、{/if}<a class="text-primary underline" href={`#plan/${id}`}>{result.verdicts.find(v => v.plan_id === id)?.ticker ?? id}</a>{/each}</p>{:else}<p class="text-sm text-muted-foreground">没有互斥组</p>{/each}
      </Section>
    {/if}
  </DataBlock>
</Section>
