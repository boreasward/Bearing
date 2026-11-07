<script lang="ts">
  import * as Dialog from '$lib/ui/dialog/index'
  import { Alert, AlertDescription } from '$lib/ui/alert/index'
  import { Button } from '$lib/ui/button/index'
  import ConfirmDialog from '$lib/ui/confirm-dialog/ConfirmDialog.svelte'
  import Field from '$lib/ui/field/Field.svelte'
  import Input from '$lib/ui/input/input.svelte'
  import { post } from '$lib/api'
  import { notify } from '$lib/ui/toast'
  import { errorText, type WritePreview } from './types'

  // 对账（spec §12 账户条）：记券商总资产，返回它与算出总资产的差额；A18 两步确认。
  let { open = $bindable(false), onDone }: { open?: boolean; onDone?: () => void } = $props()

  let draft = $state({ equity: '', asOf: '', note: '' })
  let error = $state('')
  let submitting = $state(false)

  $effect(() => {
    if (open) {
      draft = { equity: '', asOf: '', note: '' }
      error = ''
    }
  })

  let requestId = ''
  let confirmOpen = $state(false)
  let preview = $state('')
  let writing = $state(false)

  const ready = $derived(draft.equity !== '' && Number(draft.equity) > 0)

  function body(confirmed: boolean) {
    return {
      equity: Number(draft.equity),
      ...(draft.asOf.trim() === '' ? {} : { as_of: draft.asOf.trim() }),
      note: draft.note.trim(),
      request_id: requestId,
      confirmed,
    }
  }

  async function submit(event: SubmitEvent) {
    event.preventDefault()
    if (!ready || submitting) return
    submitting = true
    error = ''
    try {
      requestId = crypto.randomUUID()
      const first = await post<WritePreview>('/api/reconcile', body(false))
      preview = first.preview ?? ''
      confirmOpen = true
    } catch (cause) {
      error = errorText(cause)
    } finally {
      submitting = false
    }
  }

  async function write() {
    writing = true
    try {
      await post('/api/reconcile', body(true))
      notify.success('已记录对账')
      confirmOpen = false
      open = false
      onDone?.()
    } catch (cause) {
      confirmOpen = false
      error = errorText(cause)
    } finally {
      writing = false
    }
  }
</script>

<Dialog.Root bind:open>
  <Dialog.Content>
    <Dialog.Header>
      <Dialog.Title>对账</Dialog.Title>
      <Dialog.Description>记下券商总资产，系统算出它与账本的差额。</Dialog.Description>
    </Dialog.Header>
    <form class="grid gap-3" onsubmit={submit}>
      <Field label="券商总资产" id="reconcile-equity" required>
        {#snippet children({ id })}
          <Input {id} inputmode="decimal" class="text-right tabular-nums" bind:value={draft.equity} />
        {/snippet}
      </Field>
      <Field label="截至日期" id="reconcile-asof" hint="可空，默认当前交易日；YYYY-MM-DD">
        {#snippet children({ id })}
          <Input {id} placeholder="YYYY-MM-DD" bind:value={draft.asOf} />
        {/snippet}
      </Field>
      <Field label="备注" id="reconcile-note" hint="可空">
        {#snippet children({ id })}
          <Input {id} bind:value={draft.note} />
        {/snippet}
      </Field>
      {#if error}
        <Alert variant="danger"><AlertDescription>{error}</AlertDescription></Alert>
      {/if}
      <Dialog.Footer>
        <Button type="submit" variant="primary" busy={submitting} disabled={!ready}>提交</Button>
      </Dialog.Footer>
    </form>
  </Dialog.Content>
</Dialog.Root>

<ConfirmDialog
  bind:open={confirmOpen}
  title="确认对账"
  consequence={preview}
  actionLabel="写入"
  busy={writing}
  onConfirm={write}
  onCancel={() => (preview = '')}
/>
