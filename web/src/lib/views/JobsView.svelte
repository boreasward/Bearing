<script lang="ts">
  import { runs } from '$lib/stores'
  import PageHeader from '$lib/ui/domain/PageHeader.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import DataTable from '$lib/ui/data-table/DataTable.svelte'
  import ResourceBlock from './ResourceBlock.svelte'
  import RunStatus from './RunStatus.svelte'
  import { fmtBeijing } from '$lib/format'
  import { hrefOf } from '$lib/router'
  import type { RunsData } from '$lib/stores/types'
  type Run = RunsData['runs'][number]
  const runRows = $derived((runs.data?.runs ?? []).map((row, index) => ({ ...row, displayKey: `${row.session}:${row.started_at}:${index}` })))
  const weekdays = ['','周一','周二','周三','周四','周五','周六','周日']
  function duration(row: Run): number | null {
    if (!row.finished_at) return null
    const seconds = (new Date(row.finished_at).getTime() - new Date(row.started_at).getTime()) / 1000
    return Number.isFinite(seconds) ? seconds : null
  }
</script>
{#snippet facts(row: Run)}<StatusPill domain="run" value={row.status} /> <MetricValue value={duration(row)} unit="秒" />{/snippet}
{#snippet brief(row: Run)}<RunStatus brief={row.brief} />{/snippet}
{#snippet push(row: Run)}简报：{row.brief?.pushed_final_at ? fmtBeijing(row.brief.pushed_final_at) : '未推送'} · 周报：{row.brief?.weekly.pushed_at ? fmtBeijing(row.brief.weekly.pushed_at) : '未推送'}{/snippet}
{#snippet openBrief(row: Run)}<a class="underline" href={`${hrefOf({ kind:'view', view:'today', id:null })}?session=${encodeURIComponent(row.session)}`}>打开当天简报</a>{/snippet}
{#snippet details(row: Run)}
  <div class="grid gap-2 text-xs"><p>模型：{row.brief?.model ?? '未记录'} · 运行方式：{row.brief?.runtime ?? '未记录'}</p><p>简报耗时 <MetricValue value={row.brief?.elapsed_seconds} unit="秒" /></p><pre class="overflow-x-auto whitespace-pre-wrap font-mono">{JSON.stringify(row.brief?.usage ?? {}, null, 2)}</pre></div>
{/snippet}
<div class="grid gap-5">
  <PageHeader title="定时任务" level={1}>{#snippet meta()}每日简报计划与运行记录{/snippet}</PageHeader>
  <ResourceBlock resource={runs} emptyMessage="还没有运行记录">
    {#if runs.data}
      <Section title="计划表">
        <DataTable ariaLabel="计划表"
          columns={[
            { key:'weekdays', header:'周几', value:row => row.weekdays.map(day => weekdays[day]).join('、') },
            { key:'time', header:'时间', value:row => `${String(row.hour).padStart(2,'0')}:${String(row.minute).padStart(2,'0')}` },
            { key:'tz', header:'时区' }
          ]}
         rows={[runs.data.schedule]} rowKey={() => 'daily'} />
        <p class="text-sm">下次运行：{fmtBeijing(runs.data.schedule.next_at)}</p>
      </Section>
      <Section title="运行记录">
        <DataBlock state={runs.data.runs.length ? 'ready' : 'empty'} message="还没有运行记录">
          <DataTable ariaLabel="运行记录"
            columns={[
              { key:'session', header:'交易日' },
              { key:'trigger', header:'触发', value:row => row.trigger === 'manual' ? '手动' : '定时' },
              { key:'status', header:'事实状态与耗时', cell:facts },
              { key:'brief', header:'简报状态', cell:brief },
              { key:'push', header:'推送', cell:push },
              { key:'error', header:'错误原因', value:row => row.error ?? row.brief?.error ?? '无' },
              { key:'open', header:'简报', cell:openBrief }
            ]}
           rows={runRows} rowKey={row => row.displayKey} expandable={details} pageSize={20} />
        </DataBlock>
      </Section>
    {/if}
  </ResourceBlock>
</div>
