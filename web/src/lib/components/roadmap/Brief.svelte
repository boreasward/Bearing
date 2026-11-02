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
  // The backend writes no pending marker once a brief row exists: after a retry the readback still returns the old
  // row until the background job inserts a newer one, so the retry stays pending until a row with a larger id shows up.
  let waitingAfter = $state<number | null>(null)
  const waiting = $derived(waitingAfter != null && (detail.brief?.id ?? 0) <= waitingAfter)
  const status = $derived(waiting ? 'pending' : detail.brief_status)
  const nodeStatus = { not_requested: 'not_recorded', pending: 'running', ok: 'success', error: 'failed' } as const
  async function readback(retry = false) {
    if (busy) return
    const id = detail.version.version_id
    busy = true; error = ''
    try {
      if (retry) {
        const accepted = await retryBrief(id)
        if (!active) return
        waitingAfter = detail.brief?.id ?? 0
        onChange({ ...detail, brief_status: accepted.brief_status })
      }
      const next = await getRoadmapVersion(id)
      if (active) onChange(next)
    } catch (e) { if (active) error = describeError(e) } finally { if (active) busy = false }
  }
</script>
<Section title="实施 brief" level={3}>
  <div><StatusPill domain="node" value={nodeStatus[status]} text={BRIEF_STATUS_TEXT[status]} /></div>
  {#if status === 'ok'}
    <DataBlock state={detail.brief?.body ? 'ready' : 'empty'} message="未记录 brief 正文">
      <pre class="whitespace-pre-wrap break-words text-sm">{detail.brief?.body}</pre>
    </DataBlock>
  {:else if status === 'error'}
    <DataBlock state="degraded" message={(detail.brief?.reason && (AGENT_REASON_TEXT[detail.brief.reason] ?? detail.brief.reason)) || 'brief 生成失败'} />
  {/if}
  {#if error}<DataBlock state="error" message={error} />{/if}
  <div class="flex gap-2">
    <Button variant="outline" {busy} onclick={() => readback()}>刷新 brief</Button>
    {#if status === 'error'}<Button {busy} onclick={() => readback(true)}>重试 brief</Button>{/if}
  </div>
</Section>
