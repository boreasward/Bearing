<script lang="ts">
  import * as api from '$lib/research'
  import { describeError } from '$lib/workbench'
  import RequestForm from '$lib/components/research/RequestForm.svelte'
  import DossierList from '$lib/components/research/DossierList.svelte'
  import DossierVersion from '$lib/components/research/DossierVersion.svelte'
  import PageHeader from '$lib/ui/domain/PageHeader.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import TabNav from '$lib/ui/tab-nav/TabNav.svelte'
  import ConfirmDialog from '$lib/ui/confirm-dialog/ConfirmDialog.svelte'
  import { Button } from '$lib/ui/button/index'
  let { sub }: { sub?: string } = $props()
  const id = $derived(sub?.split('/')[0])
  let items = $state<api.ResearchItem[]>([]), detail = $state<api.ResearchDetail | null>(null)
  let loading = $state(true), error = $state(''), closing = $state(false), busy = $state(false), closeError = $state('')
  let generation = 0
  async function load() {
    const seq = ++generation, currentId = id
    loading = true; error = ''; detail = null
    try {
      if (currentId) { const next = await api.getResearch(currentId); if (seq === generation) detail = next }
      else { const next = await api.listResearch(); if (seq === generation) items = next.items }
    } catch (e) { if (seq === generation) error = describeError(e) } finally { if (seq === generation) loading = false }
  }
  $effect(() => { id; closing = false; busy = false; closeError = ''; load(); return () => { generation++ } })
  const versions = $derived(detail ? [...detail.versions].sort((a, b) => a.version_no - b.version_no) : [])
  const selected = $derived(versions.find(v => v.version_no === Number(sub?.split('/')[1])) ?? versions.at(-1))
  async function close() {
    if (!id || busy) return
    const target = id, token = generation
    busy = true; closeError = ''
    try {
      await api.closeResearch(target)
      if (token !== generation) return
      closing = false; busy = false; await load()
    } catch (e) { if (token === generation) closeError = describeError(e) } finally { if (token === generation) busy = false }
  }
</script>
<div class="grid gap-4">
  <PageHeader title="研究">{#snippet meta()}<span>只出研究档案，不自动开链</span>{/snippet}</PageHeader>
  <DataBlock state={loading ? 'loading' : error ? 'error' : !id && !items.length ? 'empty' : 'ready'} message={error || '还没有研究档案，可在下方提交请求'} onRetry={load}>
    {#if detail}
      {@const request = detail.request}
      <PageHeader title={detail.request.ticker} level={3}>
        {#snippet meta()}<StatusPill domain="research_request" value={request.status} /><span>{request.source === 'manual' ? '手动请求' : '来源未记录'} · {request.horizon_days ? `${request.horizon_days} 天` : '未指定期限'}</span>{/snippet}
        {#snippet actions()}<a class="text-primary underline" href="#research">全部档案</a>{#if request.status !== 'closed'}<Button onclick={() => { closeError = ''; closing = true }}>结束研究</Button>{/if}{/snippet}
      </PageHeader>
      <p>{detail.request.question ?? '未指定问题'}</p>
      {#if detail.request.closed_reason}<p>{api.closedReasonText(detail.request.closed_reason)}</p>{/if}
      {#if detail.chain?.head_plan_id}<a class="text-primary underline" href={`#plan/${detail.chain.head_plan_id}`}>关联计划链</a>{/if}
      <TabNav label="结论版本" items={versions.map(v => ({ href: `#research/${id}/${v.version_no}`, label: `第 ${v.version_no} 版`, current: v === selected }))} />
      {#if selected}{#key selected.version_no}<DossierVersion version={selected} />{/key}{:else}<DataBlock state="not_computed" message="请求尚未产出结论版本" />{/if}
    {:else}<Section title="研究档案" level={3}><DossierList {items} /></Section>{/if}
  </DataBlock>
  {#if !id}<RequestForm onCreated={load} />{/if}
</div>
<ConfirmDialog bind:open={closing} title="结束研究" consequence="结束档案后不再自动处理该请求，已有结论保留。" actionLabel="结束档案" destructive {busy} onConfirm={close}>
  {#if closeError}<DataBlock state="error" message={closeError} />{/if}
</ConfirmDialog>
