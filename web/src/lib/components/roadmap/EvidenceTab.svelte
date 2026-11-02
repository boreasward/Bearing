<script lang="ts">
  import type { RoadmapEvidence } from '$lib/roadmap'
  import Section from '$lib/ui/domain/Section.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import DataTable from '$lib/ui/data-table/DataTable.svelte'
  import type { Column } from '$lib/ui/data-table/types'
  let { evidence }: { evidence: RoadmapEvidence[] } = $props()
  const kinds = { manual: '手动验收', scheduled: '定时观察', user_confirmed: '用户确认' }
  const versions = $derived([...new Set(evidence.map(e => e.version_id))])
  const columns: Column<RoadmapEvidence>[] = [
    { key: 'kind', header: '种类', value: e => kinds[e.kind] }, { key: 'body', header: '内容' },
    { key: 'observed_on', header: '日期' }, { key: 'source_path', header: '来源路径' },
  ]
</script>
<DataBlock state={evidence.length ? 'ready' : 'empty'} message="尚未记录验收证据">
  {#each versions as version}<Section title={version} level={3}><DataTable {columns} rows={evidence.filter(e => e.version_id === version)} rowKey={e => e.evidence_id} ariaLabel={`${version} 证据`} /></Section>{/each}
</DataBlock>
