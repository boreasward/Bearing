<script lang="ts">
  import { onDestroy } from 'svelte'
  import { BRIEF_STATUS_TEXT, getRoadmapVersion, retryBrief, type VersionDetail } from '$lib/roadmap'
  import { AGENT_REASON_TEXT, describeError } from '$lib/workbench'
  import Section from '$lib/ui/domain/Section.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import { Button } from '$lib/ui/button/index'
  let { detail, onChange }: { detail: VersionDetail; onChange: (next: VersionDetail) => void } = $props()
  let busy = $state(false), error = $state('')
  let active = true
  onDestroy(() => { active = false })
  const nodeStatus = { not_requested: 'not_recorded', pending: 'running', ok: 'success', error: 'failed' } as const
  async function readback(retry = false) {
    if (busy) return
    const id = detail.version.version_id
    busy = true; error = ''
    try {
      if (retry) {
        const accepted = await retryBrief(id)
        if (!active) return
        onChange({ ...detail, brief_status: accepted.brief_status })
      }
      const next = await getRoadmapVersion(id)
      if (active) onChange(next)
    } catch (e) { if (active) error = describeError(e) } finally { if (active) busy = false }
  }
</script>
<Section title="实施 brief" level={3}>
  <div><StatusPill domain="node" value={nodeStatus[detail.brief_status]} text={BRIEF_STATUS_TEXT[detail.brief_status]} /></div>
  {#if detail.brief_status === 'ok'}
    <DataBlock state={detail.brief?.body ? 'ready' : 'empty'} message="未记录 brief 正文">
      <pre class="whitespace-pre-wrap break-words text-sm">{detail.brief?.body}</pre>
    </DataBlock>
  {:else if detail.brief_status === 'error'}
    <DataBlock state="degraded" message={(detail.brief?.reason && (AGENT_REASON_TEXT[detail.brief.reason] ?? detail.brief.reason)) || 'brief 生成失败'} />
  {/if}
  {#if error}<DataBlock state="error" message={error} />{/if}
  <div class="flex gap-2">
    <Button variant="outline" {busy} onclick={() => readback()}>刷新 brief</Button>
    {#if detail.brief_status === 'error'}<Button {busy} onclick={() => readback(true)}>重试 brief</Button>{/if}
  </div>
</Section>
