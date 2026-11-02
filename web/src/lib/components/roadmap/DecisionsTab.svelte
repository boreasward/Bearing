<script lang="ts">
  import { getRoadmapDecision, type RoadmapDecision, type DecisionDetail } from '$lib/roadmap'
  import { describeError } from '$lib/workbench'
  import DataTable from '$lib/ui/data-table/DataTable.svelte'
  import type { Column } from '$lib/ui/data-table/types'
  import Field from '$lib/ui/field/Field.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import VersionChip from '$lib/ui/domain/VersionChip.svelte'
  let { decisions, decisionNo }: { decisions: RoadmapDecision[]; decisionNo?: number } = $props()
  let filter = $state(''), detail = $state<DecisionDetail | null>(null), error = $state(''), loading = $state(false)
  const versions = $derived([...new Set(decisions.flatMap(d => d.version_ids))].sort())
  const rows = $derived(decisions.filter(d => !filter || d.version_ids.includes(filter)))
  $effect(() => {
    const no = decisionNo; let active = true
    detail = null; error = ''; loading = !!no
    if (no) getRoadmapDecision(no).then(d => { if (active) detail = d }).catch(e => { if (active) error = describeError(e) }).finally(() => { if (active) loading = false })
    return () => { active = false }
  })
  const columns: Column<RoadmapDecision>[] = [
    { key: 'decision_no', header: '编号', cell: link }, { key: 'decided_on', header: '日期' }, { key: 'question', header: '问题' },
    { key: 'decision', header: '决定' }, { key: 'quote', header: '用户原话' }, { key: 'version_ids', header: '关联版本', value: d => d.version_ids.join('、') },
  ]
</script>
{#snippet link(d: RoadmapDecision)}<a class="text-primary underline" href={`#roadmap/decisions/${d.decision_no}`}>#{d.decision_no}</a>{/snippet}
<div class="grid gap-3">
  <Field label="按版本筛选" id="decision-filter">{#snippet children({ id })}<select {id} bind:value={filter} class="rounded-md border border-input bg-background px-2 py-1 text-sm"><option value="">全部</option>{#each versions as version}<option value={version}>{version}</option>{/each}</select>{/snippet}</Field>
  <DataBlock state={rows.length ? 'ready' : 'empty'} message="没有符合条件的决策"><DataTable {columns} {rows} rowKey={d => d.decision_no} ariaLabel="路线决策" /></DataBlock>
  {#if decisionNo}<DataBlock state={loading ? 'loading' : error ? 'error' : detail ? 'ready' : 'empty'} message={error || '没有该决策'}>
    {#if detail}
      <Section title={`决策 #${decisionNo}`} level={3}>
        <VersionChip version={detail.decision.version} /><p>{detail.decision.decided_on}</p>
        <p>{detail.decision.question}</p><p>{detail.decision.decision}</p><blockquote>{detail.decision.quote}</blockquote>
      </Section>
      <Section title={`决策 #${decisionNo} 历史`} level={3}>
        <DataBlock state={detail.history.length ? 'ready' : 'empty'} message="没有历史修订">
          {#each detail.history as d}<details><summary>第 {d.version} 版 · {d.decided_on}</summary><p>{d.question}</p><p>{d.decision}</p><blockquote>{d.quote}</blockquote></details>{/each}
        </DataBlock>
      </Section>
    {/if}
  </DataBlock>{/if}
</div>
