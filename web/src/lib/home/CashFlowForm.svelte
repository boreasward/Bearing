<script lang="ts">
  import * as Dialog from '$lib/ui/dialog/index'
  import * as Select from '$lib/ui/select/index'
  import { Alert, AlertDescription } from '$lib/ui/alert/index'
  import { Button } from '$lib/ui/button/index'
  import ConfirmDialog from '$lib/ui/confirm-dialog/ConfirmDialog.svelte'
  import Field from '$lib/ui/field/Field.svelte'
  import Input from '$lib/ui/input/input.svelte'
  import { post } from '$lib/api'
  import { notify } from '$lib/ui/toast'
  import { errorText, type WritePreview } from './types'

  // 记出入金（spec §12 账户条）：期初 / 入金 / 出金 / 调整；A18 两步确认。
  let { open = $bindable(false), onDone }: { open?: boolean; onDone?: () => void } = $props()

  const KIND_TEXT: Record<string, string> = { opening: '期初', deposit: '入金', withdrawal: '出金', adjustment: '调整' }

  let draft = $state({ kind: 'deposit', amount: '', effectiveAt: '', note: '' })
  let error = $state('')
  let submitting = $state(false)

  $effect(() => {
    if (open) {
      draft = { kind: 'deposit', amount: '', effectiveAt: '', note: '' }
      error = ''
    }
  })

  let requestId = ''
  let confirmOpen = $state(false)
  let preview = $state('')
  let writing = $state(false)

  const ready = $derived(draft.amount !== '' && Number(draft.amount) !== 0)

  function body(confirmed: boolean) {
    return {
      kind: draft.kind,
      amount: Number(draft.amount),
      ...(draft.effectiveAt.trim() === '' ? {} : { effective_at: draft.effectiveAt.trim() }),
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
      const first = await post<WritePreview>('/api/cash-flows', body(false))
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
      await post('/api/cash-flows', body(true))
      notify.success('已记录出入金')
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
      <Dialog.Title>记出入金</Dialog.Title>
      <Dialog.Description>期初、入金、出金或调整；同一时间只有一笔有效期初。</Dialog.Description>
    </Dialog.Header>
    <form class="grid gap-3" onsubmit={submit}>
      <Field label="类型" id="cash-kind" required>
        {#snippet children({ id })}
          <Select.Root type="single" bind:value={draft.kind}>
            <Select.Trigger {id} class="w-full">{KIND_TEXT[draft.kind] ?? draft.kind}</Select.Trigger>
            <Select.Content>
              {#each Object.entries(KIND_TEXT) as [value, label] (value)}
                <Select.Item {value} {label} />
              {/each}
            </Select.Content>
          </Select.Root>
        {/snippet}
      </Field>
      <Field label="金额" id="cash-amount" required hint="调整可为负数">
        {#snippet children({ id })}
          <Input {id} inputmode="decimal" class="text-right tabular-nums" bind:value={draft.amount} />
        {/snippet}
      </Field>
      <Field label="生效日期" id="cash-at" hint="可空，默认当前交易日；YYYY-MM-DD">
        {#snippet children({ id })}
          <Input {id} placeholder="YYYY-MM-DD" bind:value={draft.effectiveAt} />
        {/snippet}
      </Field>
      <Field label="备注" id="cash-note" hint="可空">
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
  title="确认记出入金"
  consequence={preview}
  actionLabel="写入"
  busy={writing}
  onConfirm={write}
  onCancel={() => (preview = '')}
/>
