<script lang="ts">
  import * as Dialog from '$lib/ui/dialog/index'
  import * as Select from '$lib/ui/select/index'
  import { Alert, AlertDescription } from '$lib/ui/alert/index'
  import { Button } from '$lib/ui/button/index'
  import Field from '$lib/ui/field/Field.svelte'
  import Input from '$lib/ui/input/input.svelte'
  import ConfirmDialog from '$lib/ui/confirm-dialog/ConfirmDialog.svelte'
  import { post } from '$lib/api'
  import { notify } from '$lib/ui/toast'
  import { errorText, type WritePreview } from './types'

  // 记成交（spec §12 计划与持仓块 / A18）：表单预填股票、计划和建议股数；提交先拿
  // preview（confirmed=false），ConfirmDialog 复述确认后才 confirmed=true 写入。
  let {
    open = $bindable(false),
    ticker = '',
    planId = null as number | null,
    shares = null as number | null,
    onDone,
  }: {
    open?: boolean
    ticker?: string
    planId?: number | null
    shares?: number | null
    onDone?: () => void
  } = $props()

  let draft = $state({ ticker: '', side: 'buy', quantity: '', price: '', executedAt: '', fee: '', note: '' })
  let error = $state('')
  let submitting = $state(false)

  // every opening starts from the current prefill; the time defaults to now (with timezone)
  $effect(() => {
    if (open) {
      draft = {
        ticker,
        side: 'buy',
        quantity: shares == null ? '' : String(shares),
        price: '',
        executedAt: new Date().toISOString(),
        fee: '',
        note: '',
      }
      error = ''
    }
  })

  let requestId = ''
  let confirmOpen = $state(false)
  let preview = $state('')
  let writing = $state(false)

  // 手续费可空（默认 0）；填了就必须是数字且不为负——NaN / 负数不进请求体，提交按钮先拦下
  const feeNumber = $derived((draft.fee.trim() === '' ? 0 : Number(draft.fee)))
  const ready = $derived(
    draft.ticker.trim() !== '' &&
      draft.quantity !== '' &&
      Number(draft.quantity) > 0 &&
      draft.price !== '' &&
      Number(draft.price) > 0 &&
      draft.executedAt.trim() !== '' &&
      Number.isFinite(feeNumber) &&
      feeNumber >= 0,
  )

  function body(confirmed: boolean) {
    return {
      ticker: draft.ticker.trim(),
      side: draft.side,
      quantity: Number(draft.quantity),
      price: Number(draft.price),
      executed_at: draft.executedAt.trim(),
      fee: feeNumber,
      note: draft.note.trim(),
      ...(planId == null ? {} : { plan_id: planId }),
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
      const first = await post<WritePreview>('/api/trades', body(false))
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
      await post('/api/trades', body(true))
      notify.success('已记录成交')
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
      <Dialog.Title>记成交</Dialog.Title>
      <Dialog.Description>记录一笔买入或卖出；买入会关联或补录计划并给出止损单指令。</Dialog.Description>
    </Dialog.Header>
    <form class="grid gap-3" onsubmit={submit}>
      <Field label="股票" id="trade-ticker" required>
        {#snippet children({ id })}
          <Input {id} bind:value={draft.ticker} />
        {/snippet}
      </Field>
      <Field label="方向" id="trade-side" required>
        {#snippet children({ id })}
          <Select.Root type="single" bind:value={draft.side}>
            <Select.Trigger {id} class="w-full">{draft.side === 'buy' ? '买入' : '卖出'}</Select.Trigger>
            <Select.Content>
              <Select.Item value="buy" label="买入" />
              <Select.Item value="sell" label="卖出" />
            </Select.Content>
          </Select.Root>
        {/snippet}
      </Field>
      <Field label="股数" id="trade-quantity" required hint="买入时关联到计划的建议股数">
        {#snippet children({ id })}
          <Input {id} inputmode="decimal" class="text-right tabular-nums" bind:value={draft.quantity} />
        {/snippet}
      </Field>
      <Field label="价格" id="trade-price" required hint="每股成交价">
        {#snippet children({ id })}
          <Input {id} inputmode="decimal" class="text-right tabular-nums" bind:value={draft.price} />
        {/snippet}
      </Field>
      <Field label="时间" id="trade-at" required hint="带时区的 ISO 时间，如 2026-11-06T10:30:00-05:00">
        {#snippet children({ id })}
          <Input {id} bind:value={draft.executedAt} />
        {/snippet}
      </Field>
      <Field label="手续费" id="trade-fee" hint="可空，默认 0">
        {#snippet children({ id })}
          <Input {id} inputmode="decimal" class="text-right tabular-nums" bind:value={draft.fee} />
        {/snippet}
      </Field>
      <Field label="备注" id="trade-note" hint="可空">
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
  title="确认记成交"
  consequence={preview}
  actionLabel="写入"
  busy={writing}
  onConfirm={write}
  onCancel={() => (preview = '')}
/>
