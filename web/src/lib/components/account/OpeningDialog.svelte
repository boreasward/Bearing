<script lang="ts">
  import { onDestroy } from 'svelte'
  import { openAccount, describeAccountError, type Account } from '$lib/accounts'
  import { newRequestId } from '$lib/workbench'
  import { Button } from '$lib/ui/button/index'
  import * as Dialog from '$lib/ui/dialog/index'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import Field from '$lib/ui/field/Field.svelte'
  import { Input } from '$lib/ui/input/index'

  let { open = $bindable(false), account, onopened }: { open: boolean; account: Account; onopened: (account: Account) => void } = $props()
  const uid = $props.id()
  let cash = $state('')
  let date = $state('')
  let positions = $state<{ ticker: string; quantity: string; cost_per_share: string }[]>([])
  let step = $state(0)
  let error = $state('')
  let busy = $state(false)
  let requestId = newRequestId()
  let generation = 0
  let alive = true
  let wasOpen = false
  let previousId = ''
  onDestroy(() => { alive = false; generation++ })
  $effect(() => {
    const id = account.id
    if (open !== wasOpen || id !== previousId) {
      generation++; busy = false; step = 0; error = ''
      if (id !== previousId) { cash = ''; date = ''; positions = [] }
      if (open) requestId = newRequestId()
      previousId = id; wasOpen = open
    }
  })
  const title = $derived(step === 0 ? '真实账户开账' : step === 1 ? '确认期初值' : '开账后不可修改')
  function review(event: Event) {
    event.preventDefault(); error = ''
    if (!Number.isFinite(Number(cash)) || !(Number(cash) > 0)) { error = '期初现金须大于 0 且为有限数值'; return }
    if (!date) { error = '请填写开账日'; return }
    if (positions.some(p => !p.ticker.trim() || !Number.isFinite(Number(p.quantity)) || !Number.isFinite(Number(p.cost_per_share)) || !(Number(p.quantity) > 0) || !(Number(p.cost_per_share) > 0))) { error = '每笔期初持仓须填写代码、正股数与正成本'; return }
    if (new Set(positions.map(p => p.ticker.trim().toUpperCase())).size !== positions.length) { error = '同一代码只能填写一笔期初持仓'; return }
    step = 1
  }
  async function submit() {
    if (busy || step !== 2) return
    const operation = generation, targetId = account.id
    const current = () => alive && open && operation === generation && targetId === account.id
    busy = true; error = ''
    try {
      const result = await openAccount(targetId, { request_id: requestId, opening_cash: cash.trim(), opened_on: date,
        ...(positions.length ? { opening_positions: positions.map(p => ({ ticker: p.ticker.trim().toUpperCase(), quantity: p.quantity.trim(), cost_per_share: p.cost_per_share.trim() })) } : {}) })
      if (!current()) return
      open = false; onopened(result.account); step = 0; cash = ''; date = ''; positions = []; requestId = newRequestId()
    } catch (err) { if (current()) error = describeAccountError(err) }
    finally { if (current()) busy = false }
  }
</script>
<Dialog.Root bind:open>
  <Dialog.Content class="max-h-[90vh] overflow-y-auto" showCloseButton={!busy} interactOutsideBehavior={busy ? 'ignore' : 'close'} escapeKeydownBehavior={busy ? 'ignore' : 'close'}>
    <Dialog.Header><Dialog.Title>{title}</Dialog.Title><Dialog.Description>记录 {account.name} 的期初现金与已有持仓。已有成交、出入金必须发生在开账日或之后。</Dialog.Description></Dialog.Header>
    {#if step === 0}
      <form class="grid gap-3" onsubmit={review}>
        <Field label="期初现金" id={`${uid}-cash`}>{#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} inputmode="decimal" class="text-right tabular-nums" bind:value={cash} />{/snippet}</Field>
        <Field label="开账日" id={`${uid}-date`}>{#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} type="date" bind:value={date} />{/snippet}</Field>
        <p class="text-xs text-muted-foreground">期初持仓按实物入账，不会扣减期初现金。</p>
        {#each positions as position, index}
          <div class="grid gap-2 rounded-md border border-border p-3 sm:grid-cols-3">
            <Field label={`期初代码 ${index + 1}`} id={`${uid}-ticker-${index}`}>
              {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} bind:value={position.ticker} />{/snippet}
            </Field>
            <Field label={`期初股数 ${index + 1}`} id={`${uid}-quantity-${index}`}>
              {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} inputmode="decimal" class="text-right tabular-nums" bind:value={position.quantity} />{/snippet}
            </Field>
            <Field label={`期初成本 ${index + 1}`} id={`${uid}-cost-${index}`}>
              {#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} inputmode="decimal" class="text-right tabular-nums" bind:value={position.cost_per_share} />{/snippet}
            </Field>
            <Button size="sm" variant="ghost" onclick={() => positions = positions.filter((_, i) => i !== index)}>移除期初持仓 {index + 1}</Button>
          </div>
        {/each}
        <div><Button variant="outline" onclick={() => positions = [...positions, { ticker: '', quantity: '', cost_per_share: '' }]}>添加期初持仓</Button></div>
        {#if error}<DataBlock state="error" message={error} />{/if}
        <Dialog.Footer><Button variant="outline" onclick={() => open = false}>取消</Button><Button type="submit" variant="primary">核对期初值</Button></Dialog.Footer>
      </form>
    {:else}
      <div class="grid gap-2 text-sm"><p>开账日 {date} · 期初现金 <span class="break-all tabular-nums">{cash}</span></p>
        {#each positions as p}<p class="break-all">{p.ticker.toUpperCase()} · {p.quantity} 股 · 每股成本 {p.cost_per_share}</p>{/each}
        <p class="text-muted-foreground">期初持仓按实物入账，不会扣减期初现金。</p>
        {#if step === 2}<p class="font-medium">确认后期初值不可修改，请以券商账单核对。</p>{/if}
      </div>
      {#if error}<DataBlock state="error" message={error} />{/if}
      <Dialog.Footer><Button variant="outline" disabled={busy} onclick={() => step = 0}>返回修改</Button>
        {#if step === 1}<Button variant="primary" onclick={() => step = 2}>期初值正确，继续</Button>{:else}<Button variant="primary" {busy} onclick={submit}>确认开账</Button>{/if}
      </Dialog.Footer>
    {/if}
  </Dialog.Content>
</Dialog.Root>
