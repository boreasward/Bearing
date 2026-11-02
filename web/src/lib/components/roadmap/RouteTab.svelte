<script lang="ts">
  import { getRoadmapVersion, type RoadmapGoal, type RoadmapVersion, type VersionDetail } from '$lib/roadmap'
  import { describeError } from '$lib/workbench'
  import DataTable from '$lib/ui/data-table/DataTable.svelte'
  import type { Column } from '$lib/ui/data-table/types'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import VersionChip from '$lib/ui/domain/VersionChip.svelte'
  import Brief from './Brief.svelte'
  let { goals, versions, versionId }: { goals: RoadmapGoal[]; versions: RoadmapVersion[]; versionId?: string } = $props()
  let detail = $state<VersionDetail | null>(null), error = $state(''), loading = $state(false)
  const selected = $derived(detail?.version)
  $effect(() => {
    const id = versionId; let active = true
    detail = null; error = ''; loading = !!id
    if (id) getRoadmapVersion(id).then(v => { if (active) detail = v }).catch(e => { if (active) error = describeError(e) }).finally(() => { if (active) loading = false })
    return () => { active = false }
  })
  const columns: Column<RoadmapVersion>[] = [
    { key: 'version_id', header: '版本', cell: link }, { key: 'title', header: '标题' }, { key: 'theme', header: '主题' },
    { key: 'status', header: '状态', cell: status }, { key: 'depends_on', header: '依赖', value: v => v.depends_on.join('、') || '无' },
  ]
</script>
{#snippet link(v: RoadmapVersion)}<a class="text-primary underline" href={`#roadmap/versions/${encodeURIComponent(v.version_id)}`}>{v.version_id}</a>{/snippet}
{#snippet status(v: RoadmapVersion)}<StatusPill domain="roadmap_version" value={v.status} />{/snippet}
<div class="grid gap-4">
  <Section title="目标与主指标" level={3}><DataBlock state={goals.length ? 'ready' : 'empty'} message="尚未导入目标">{#each goals as goal}<p class="whitespace-pre-wrap">{goal.body}</p>{/each}</DataBlock></Section>
  <DataBlock state={versions.length ? 'ready' : 'empty'} message="尚未导入路线版本"><DataTable {columns} rows={versions} rowKey={v => v.version_id} ariaLabel="路线版本" /></DataBlock>
  {#if versionId}<DataBlock state={loading ? 'loading' : error ? 'error' : selected ? 'ready' : 'empty'} message={error || '没有该版本'}>
    {#if selected}<Section title={`${selected.version_id} · ${selected.title}`} level={3}>
      <VersionChip version={selected.version} />
      <h4 class="text-sm font-medium">范围</h4><p class="whitespace-pre-wrap">{selected.scope || '未记录范围'}</p>
      <h4 class="text-sm font-medium">退出条件</h4><p class="whitespace-pre-wrap">{selected.exit_conditions || '未记录退出条件'}</p>
      <p class="text-xs text-muted-foreground">{[selected.spec_path, selected.plan_path, selected.tag].filter(Boolean).join(' · ')}</p>
    </Section>{/if}
    {#if detail}
      <Section title="版本历史" level={3}>
        <DataBlock state={detail.history.length ? 'ready' : 'empty'} message="没有历史修订">
          {#each detail.history as entry}<details class="rounded-md border border-border p-2"><summary>第 {entry.version} 版 · {entry.title}</summary><p class="whitespace-pre-wrap">{entry.scope}</p><p>{entry.exit_conditions}</p><StatusPill domain="roadmap_version" value={entry.status} /></details>{/each}
        </DataBlock>
      </Section>
      {#key detail.version.version_id}<Brief {detail} onChange={next => detail = next} />{/key}
    {/if}
  </DataBlock>{/if}
</div>
