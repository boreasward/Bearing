<script lang="ts">
  import * as api from '$lib/roadmap'
  import { describeError } from '$lib/workbench'
  import ProposalDecide from './ProposalDecide.svelte'
  import Brief from './Brief.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import { Button } from '$lib/ui/button/index'
  import * as Card from '$lib/ui/card/index'
  import Field from '$lib/ui/field/Field.svelte'
  let { versions, selectedId, onChange }: { versions: api.RoadmapVersion[]; selectedId?: number; onChange: () => void } = $props()
  let items = $state<api.RoadmapProposal[]>([]), filter = $state(''), loading = $state(true), error = $state('')
  let deciding = $state<{ proposal: api.RoadmapProposal; decision: 'approved' | 'rejected' } | null>(null)
  let briefs = $state<Record<string, api.VersionDetail>>({}), briefErrors = $state<Record<string, string>>({}), briefBusy = $state<Record<string, boolean>>({})
  let outcomes = $state<Record<number, api.ProposalDecisionResult | undefined>>({})
  let generation = 0
  async function load() {
    const seq = ++generation; loading = true; error = ''
    try { const v = await api.listProposals(filter); if (seq === generation) items = v.items }
    catch (e) { if (seq === generation) error = describeError(e) } finally { if (seq === generation) loading = false }
  }
  $effect(() => { filter; load(); return () => { generation++ } })
  const SOURCE_TEXT = { data: '数据', agent_quality: 'Agent 质量', route: '路线' }
  const versionOf = (p: api.RoadmapProposal) => outcomes[p.id]?.version_id ?? versions.find(v => v.proposal_id === p.id)?.version_id
  async function loadBrief(id: string) {
    if (briefBusy[id]) return
    briefBusy[id] = true; briefErrors[id] = ''
    try { briefs[id] = await api.getRoadmapVersion(id) }
    catch (e) { briefErrors[id] = describeError(e) } finally { briefBusy[id] = false }
  }
</script>
<div class="grid gap-3">
  <Field label="提案状态" id="proposal-filter">{#snippet children({ id })}<select {id} bind:value={filter} class="rounded-md border border-input bg-background px-2 py-1 text-sm"><option value="">全部</option><option value="proposed">待决定</option><option value="approved">已批准</option><option value="rejected">已驳回</option><option value="superseded">已取代</option></select>{/snippet}</Field>
  <DataBlock state={loading ? 'loading' : error ? 'error' : !items.length ? 'empty' : 'ready'} message={error || '没有符合条件的提案'} onRetry={load}>
    {#each items.filter(p => !selectedId || p.id === selectedId) as p (p.id)}
      {@const outcome = outcomes[p.id]}
      {@const status = outcome?.status ?? p.status}
      {@const version = versionOf(p)}
      <Card.Root><Card.Content class="grid gap-3">
        <div class="flex flex-wrap items-center gap-2"><a href={`#roadmap/proposals/${p.id}`} class="font-medium text-primary underline">{p.title}</a><StatusPill domain="proposal" value={status} /><span class="text-xs text-muted-foreground">{SOURCE_TEXT[p.source]}</span></div>
        <Section title="改什么" level={3}><p class="whitespace-pre-wrap">{p.change}</p></Section>
        <Section title="依据" level={3}><p class="whitespace-pre-wrap">{p.rationale}</p>{#each p.evidence_refs as ref}
          {@const evidence = p.evidence_rows.find(row => row.ref === ref)}
          <details><summary class="cursor-pointer text-sm">{ref}</summary>
            {#if evidence}<pre class="whitespace-pre-wrap break-words text-sm">{JSON.stringify(evidence.body, null, 2)}</pre>
            {:else}<p class="text-xs text-muted-foreground">{p.evidence_note ?? '历史证据包缺少该引用的记录'}</p>{/if}
          </details>
        {/each}{#if !p.evidence_refs.length && p.evidence_note}<p class="text-xs text-muted-foreground">{p.evidence_note}</p>{/if}</Section>
        <Section title="预期与验收" level={3}><p>{p.expected ?? '未记录预期'}</p><p>{p.acceptance ?? '未记录验收'}</p></Section>
        {#if p.decision_reason}<p>决定理由：{p.decision_reason}</p>{/if}
        {#if status === 'proposed'}<div class="flex gap-2"><Button onclick={() => deciding = { proposal: p, decision: 'approved' }}>批准</Button><Button variant="outline" onclick={() => deciding = { proposal: p, decision: 'rejected' }}>驳回</Button></div>{/if}
        {#if status === 'approved'}
          {#if version}
            <a class="text-primary underline" href={`#roadmap/versions/${encodeURIComponent(version)}`}>路线版本 {version}</a>
            <div><Button variant="outline" onclick={() => loadBrief(version)} busy={briefBusy[version]}>查看实施 brief</Button></div>
            {#if briefErrors[version]}<DataBlock state="error" message={briefErrors[version]} onRetry={() => loadBrief(version)} />{/if}
            {#if briefs[version]}{#key version}<Brief detail={briefs[version]} onChange={next => briefs[version] = next} />{/key}
            {:else if outcome?.brief_status}<p>{api.BRIEF_STATUS_TEXT[outcome.brief_status]}</p>{/if}
          {:else}<DataBlock state="not_computed" message="未找到该提案关联的路线版本记录" />{/if}
        {/if}
      </Card.Content></Card.Root>
    {/each}
  </DataBlock>
</div>
{#if deciding}<ProposalDecide proposal={deciding.proposal} decision={deciding.decision} defaultVersion={api.nextVersionId(versions)} onCancel={() => deciding = null} onDone={result => { outcomes[deciding!.proposal.id] = result; deciding = null; onChange(); load(); if (result.version_id) loadBrief(result.version_id) }} />{/if}
