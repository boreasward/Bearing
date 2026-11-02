<script lang="ts">
  import { untrack } from 'svelte'
  import { decideProposal, type ProposalDecisionResult, type RoadmapProposal } from '$lib/roadmap'
  import { describeError, newRequestId } from '$lib/workbench'
  import ConfirmDialog from '$lib/ui/confirm-dialog/ConfirmDialog.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import Field from '$lib/ui/field/Field.svelte'
  import { Input } from '$lib/ui/input/index'
  import { Textarea } from '$lib/ui/textarea/index'
  let { proposal, decision, defaultVersion, onDone, onCancel }: { proposal: RoadmapProposal; decision: 'approved' | 'rejected'; defaultVersion: string; onDone: (r: ProposalDecisionResult) => void; onCancel: () => void } = $props()
  let open = $state(true), reason = $state(''), versionId = $state(untrack(() => defaultVersion)), busy = $state(false), error = $state('')
  const requestId = newRequestId()
  const valid = $derived(!!reason.trim() && (decision === 'rejected' || /^v\d+\.\d+(?:\.\d+)?$/.test(versionId.trim())))
  async function submit() {
    if (busy || !valid) return
    busy = true; error = ''
    try { const result = await decideProposal(proposal.id, { decision, ...(decision === 'approved' ? { version_id: versionId.trim() } : {}), reason: reason.trim(), request_id: requestId }); onDone(result) }
    catch (e) { error = describeError(e) } finally { busy = false }
  }
</script>
<ConfirmDialog bind:open title={`${decision === 'approved' ? '批准' : '驳回'}：${proposal.title}`} consequence={decision === 'approved' ? '批准后创建路线条目并后台起草实施 brief。' : '记录驳回理由，提案保留供追溯。'} actionLabel={decision === 'approved' ? '批准提案' : '驳回提案'} destructive={decision === 'rejected'} {busy} confirmDisabled={!valid} onConfirm={submit} {onCancel}>
  {#if decision === 'approved'}<Field label="版本编号" id="proposal-version" hint="使用尚未占用的版本编号，例如 v2.16">{#snippet children({ id, describedby })}<Input {id} bind:value={versionId} aria-describedby={describedby} />{/snippet}</Field>{/if}
  <Field label="理由" id="proposal-reason">{#snippet children({ id })}<Textarea {id} bind:value={reason} required />{/snippet}</Field>
  {#if error}<DataBlock state="error" message={error} />{/if}
</ConfirmDialog>
