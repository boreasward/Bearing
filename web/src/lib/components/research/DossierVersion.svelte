<script lang="ts">
  import type { DossierEntry, ResearchVersion } from '$lib/research'
  import { COUNTER_STATUS_TEXT, DOSSIER_ACTION_TEXT, RESEARCH_PHASE_TEXT, versionSummaryText } from '$lib/research'
  import { AGENT_REASON_TEXT } from '$lib/workbench'
  import AgentReasoning from '$lib/components/AgentReasoning.svelte'
  import Facts from './Facts.svelte'
  import { Button } from '$lib/ui/button/index'
  import Section from '$lib/ui/domain/Section.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import CutoffTime from '$lib/ui/domain/CutoffTime.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  let { version }: { version: ResearchVersion } = $props()
  let reasoning = $state(false)
  const output = $derived(version.output)
  const phase = $derived(output?.research ?? null)
  const reasonText = (reason: string) => AGENT_REASON_TEXT[reason] ?? reason
  const reviewWhen = $derived(Array.isArray(version.review_when) ? version.review_when : [])
</script>

{#snippet entries(title: string, list: DossierEntry[] | undefined, none: string)}
  <Section {title} level={3}>
    <DataBlock state={list == null ? 'not_computed' : list.length ? 'ready' : 'empty'} message={list == null ? '本版未记录' : none}>
      <ul class="grid gap-1 text-sm">{#each list ?? [] as entry}<li>{entry.text} {#if entry.refs.length}<span class="text-xs text-muted-foreground">{entry.refs.join('、')}</span>{/if}</li>{/each}</ul>
    </DataBlock>
    {#if title === '反证与替代解释' && output?.counter_status}<p class="text-xs text-muted-foreground">{COUNTER_STATUS_TEXT[output.counter_status] ?? output.counter_status}</p>{/if}
  </Section>
{/snippet}
<div class="grid gap-4">
  <div class="flex flex-wrap gap-3 text-xs"><StatusPill domain="research_request" value={version.status} /><CutoffTime at={version.cutoff_at} /><span>token {version.tokens ?? '未记录'} · {version.elapsed_ms == null ? '耗时未记录' : `${version.elapsed_ms / 1000} 秒`}</span></div>
  <Section title="论点" level={3}>
    <p>{version.summary == null ? '尚无结论' : versionSummaryText(version.summary)}</p>
    {#if version.action}<p class="text-xs text-muted-foreground">档案结论：{DOSSIER_ACTION_TEXT[version.action] ?? version.action}</p>{/if}
  </Section>
  <Section title="事实与来源" level={3}>
    {#if phase?.output}<Facts output={phase.output} />
    {:else}<DataBlock state="not_computed" message={phase ? `${RESEARCH_PHASE_TEXT[phase.status]}${phase.reason ? `：${reasonText(phase.reason)}` : ''}` : '本版未记录研究事实'} />{/if}
  </Section>
  {@render entries('催化', output?.catalysts, '本版没有催化条目')}
  {@render entries('反证与替代解释', output?.counter, '本版没有反证条目')}
  {@render entries('价格与情景', output?.price_scenarios, '本版没有情景条目')}
  {@render entries('下一步', output?.next_steps, '本版没有下一步条目')}
  <Section title="下一次复查条件" level={3}>
    {#if reviewWhen.length}<pre class="whitespace-pre-wrap text-sm">{JSON.stringify(reviewWhen, null, 2)}</pre>{:else}<p class="text-sm">本版未记录</p>{/if}
  </Section>
  {#if version.agent_run_id}
    <div><Button variant="outline" aria-expanded={reasoning} onclick={() => reasoning = !reasoning}>看推理过程</Button></div>
    {#if reasoning}{#key version.agent_run_id}<AgentReasoning runId={version.agent_run_id} />{/key}{/if}
  {/if}
</div>
