<script lang="ts">
  import { getRoadmap, type RoadmapView } from '$lib/roadmap'
  import { describeError } from '$lib/workbench'
  import PageHeader from '$lib/ui/domain/PageHeader.svelte'
  import CutoffTime from '$lib/ui/domain/CutoffTime.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import TabNav from '$lib/ui/tab-nav/TabNav.svelte'
  import RouteTab from '$lib/components/roadmap/RouteTab.svelte'
  import ProposalsTab from '$lib/components/roadmap/ProposalsTab.svelte'
  import DecisionsTab from '$lib/components/roadmap/DecisionsTab.svelte'
  import EvidenceTab from '$lib/components/roadmap/EvidenceTab.svelte'
  let { sub = '' }: { sub?: string } = $props()
  const tab = $derived(['proposals', 'decisions', 'evidence'].includes(sub.split('/')[0]) ? sub.split('/')[0] : 'route')
  const tabs = [ ['route', '路线'], ['proposals', '提案'], ['decisions', '决策'], ['evidence', '证据'] ]
  let data = $state<RoadmapView | null>(null), error = $state('')
  let generation = 0
  async function load() {
    const seq = ++generation; error = ''
    try { const next = await getRoadmap(); if (seq === generation) data = next } catch (e) { if (seq === generation) error = describeError(e) }
  }
  $effect(() => { load(); return () => { generation++ } })
</script>
<div class="grid gap-4">
  <PageHeader title="路线">
    {#snippet meta()}{#if data}<CutoffTime at={data.exported_at} label="读取时间" />{/if}{/snippet}
    {#snippet actions()}<a class="text-primary underline" href="/api/roadmap/export.md" download>导出 Markdown</a>{/snippet}
  </PageHeader>
  <TabNav label="路线页签" items={tabs.map(([id, label]) => ({ href: id === 'route' ? '#roadmap' : `#roadmap/${id}`, label, current: tab === id }))} />
  <DataBlock state={error ? 'error' : data ? 'ready' : 'loading'} message={error} onRetry={load}>
    {#if data}
      {#if tab === 'proposals'}<ProposalsTab versions={data.versions} selectedId={Number(sub.split('/')[1]) || undefined} onChange={load} />
      {:else if tab === 'decisions'}<DecisionsTab decisions={data.decisions} decisionNo={Number(sub.split('/')[1]) || undefined} />
      {:else if tab === 'evidence'}<EvidenceTab evidence={data.evidence} />
      {:else}<RouteTab goals={data.goals} versions={data.versions} versionId={sub.startsWith('versions/') ? decodeURIComponent(sub.slice(9)) : undefined} />{/if}
    {/if}
  </DataBlock>
</div>
