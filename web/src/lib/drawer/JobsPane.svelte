<script lang="ts">
  import { runs, research } from '$lib/stores'
  import Section from '$lib/ui/domain/Section.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import { Button } from '$lib/ui/button/index'
  import ResourceBlock from '$lib/views/ResourceBlock.svelte'
  import RunStatus from '$lib/views/RunStatus.svelte'
  import { fmtBeijing } from '$lib/format'
  import type { ContextChip } from '$lib/views/context'
  let { onBring }: { onBring(chip: ContextChip): void } = $props()
  const watching = research('watching')
  const recent = $derived(runs.data?.runs.slice(0, 4) ?? [])
  const week = $derived.by(() => {
    void runs.data
    const parts = new Intl.DateTimeFormat('en-CA', { timeZone:'America/New_York', year:'numeric', month:'2-digit', day:'2-digit' }).formatToParts(new Date())
    const get = (key: string) => parts.find(part => part.type === key)?.value ?? ''
    const start = new Date(`${get('year')}-${get('month')}-${get('day')}T00:00:00Z`)
    start.setUTCDate(start.getUTCDate() - (start.getUTCDay() + 6) % 7)
    const end = new Date(start); end.setUTCDate(end.getUTCDate() + 4)
    return { start:start.toISOString().slice(0, 10), end:end.toISOString().slice(0, 10) }
  })
  const weekly = $derived(runs.data?.runs.find(row => row.session >= week.start && row.session <= week.end && row.brief?.weekly.present))
  function condition(row: NonNullable<typeof watching.data>['research'][number]): string {
    if (row.triggered_session) return '条件已到，待复看'
    if (!row.condition) return '尚无条件'
    if (row.missing_bar) return '缺日线，未复查'
    if (row.condition_met_today) return '条件已到，待复看'
    return row.condition_met_today === null ? '日线不足，未检查' : '观察中'
  }
</script>
<div class="grid gap-5 text-sm">
  <Section title="每日简报">
    {#snippet actions()}<a href="#jobs" aria-label="打开定时任务">›</a>{/snippet}
    <ResourceBlock resource={runs} emptyMessage="还没有运行记录">
      <p>下次 {fmtBeijing(runs.data?.schedule.next_at)}</p>
      <DataBlock state={recent.length ? 'ready' : 'empty'} message="还没有运行记录"><ul class="grid gap-2">{#each recent as row}<li class="flex flex-wrap items-center gap-1">{row.session} <RunStatus brief={row.brief} /></li>{/each}</ul></DataBlock>
    </ResourceBlock>
  </Section>
  <Section title="研究复查">
    {#snippet actions()}<a href="#jobs" aria-label="打开研究复查任务">›</a>{/snippet}
    <ResourceBlock resource={watching} emptyMessage="没有观察中的研究">
      <DataBlock state={watching.data?.research.length ? 'ready' : 'empty'} message="没有观察中的研究">
        <ul class="grid gap-2">{#each watching.data?.research ?? [] as row (row.research_id)}<li><p>{row.ticker} · 第 {row.day} 天 · {condition(row)}</p><Button size="sm" variant="ghost" onclick={() => onBring({ kind:'research', ticker:row.ticker, id:row.research_id })}>带进对话</Button></li>{/each}</ul>
      </DataBlock>
    </ResourceBlock>
  </Section>
  <Section title="周报">
    {#snippet actions()}<a href="#jobs" aria-label="打开周报任务">›</a>{/snippet}
    <ResourceBlock resource={runs} emptyMessage="还没有运行记录">
      <p>本周窗口：{week.start} 至 {week.end}</p><p>{weekly ? `已生成 · ${weekly.brief?.weekly.pushed_at ? '已推送' : '未推送'}` : '本周还没有周报'}</p>
    </ResourceBlock>
  </Section>
</div>
