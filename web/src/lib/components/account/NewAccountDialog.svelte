<script lang="ts">
  import { onDestroy } from 'svelte'
  import { createAccount, describeAccountError, STRATEGY_TEXT, type Account, type AccountParams, type CreateAccountBody } from '$lib/accounts'
  import { newRequestId } from '$lib/workbench'
  import { Button } from '$lib/ui/button/index'
  import * as Dialog from '$lib/ui/dialog/index'
  import * as Select from '$lib/ui/select/index'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import Field from '$lib/ui/field/Field.svelte'
  import { Input } from '$lib/ui/input/index'

  let { open = $bindable(false), oncreated }: { open: boolean; oncreated: (account: Account) => void } = $props()
  const uid = $props.id()
  let name = $state('')
  let strategy = $state<'agent_managed' | 'follow_plans'>('agent_managed')
  let cash = $state('100000')
  let error = $state('')
  let busy = $state(false)
  let requestId = newRequestId()
  let params = $state<Record<string, string>>({})
  let generation = 0
  let alive = true
  let wasOpen = false
  onDestroy(() => { alive = false; generation++ })
  $effect(() => {
    if (open !== wasOpen) {
      generation++; wasOpen = open; busy = false; error = ''
      if (open) requestId = newRequestId()
    }
  })
  const fields = [
    ['risk_pct', '单笔风险比例'], ['single_cap', '单票上限比例'], ['total_cap', '总持仓上限比例'],
    ['fee_per_trade', '每笔费用'], ['max_positions', '最多持仓数'], ['reconcile_tolerance_pct', '对账容差比例'], ['reconcile_tolerance_abs', '对账容差金额'],
  ]
  async function submit(event: Event) {
    event.preventDefault()
    if (busy) return
    error = ''
    if (!name.trim()) { error = '请填写账户名称'; return }
    if (!Number.isFinite(Number(cash)) || !(Number(cash) > 0)) { error = '初始现金须大于 0 且为有限数值'; return }
    const specified = Object.fromEntries(Object.entries(params).filter(([, value]) => value?.trim()).map(([key, value]) => [key, key === 'max_positions' ? Number(value) : value.trim()])) as AccountParams
    const body: CreateAccountBody = { request_id: requestId, name: name.trim(), strategy, opening_cash: cash.trim(), ...(Object.keys(specified).length ? { params: specified } : {}) }
    const operation = generation
    const current = () => alive && open && operation === generation
    busy = true
    try {
      const result = await createAccount(body)
      if (!current()) return
      open = false
      oncreated(result.account)
      name = ''; params = {}; cash = '100000'; requestId = newRequestId()
    } catch (err) { if (current()) error = describeAccountError(err) }
    finally { if (current()) busy = false }
  }
</script>
<Dialog.Root bind:open>
  <Dialog.Content class="max-h-[90vh] overflow-y-auto" showCloseButton={!busy} interactOutsideBehavior={busy ? 'ignore' : 'close'} escapeKeydownBehavior={busy ? 'ignore' : 'close'}>
    <Dialog.Header><Dialog.Title>新建模拟账户</Dialog.Title><Dialog.Description>按独立现金与持仓运行。模拟成交，无滑点。留空参数使用当前配置，开户日由后端取下一交易日。</Dialog.Description></Dialog.Header>
    <form class="grid gap-3" onsubmit={submit}>
      <Field label="账户名称" id={`${uid}-name`}>{#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} bind:value={name} disabled={busy} />{/snippet}</Field>
      <Field label="策略" id={`${uid}-strategy`}>{#snippet children({ id, describedby })}
        <Select.Root type="single" bind:value={strategy} disabled={busy}>
          <Select.Trigger {id} aria-describedby={describedby} class="w-full">{STRATEGY_TEXT[strategy]}</Select.Trigger>
          <Select.Content><Select.Item value="agent_managed" label="Agent 自管" /><Select.Item value="follow_plans" label="照计划执行" /></Select.Content>
        </Select.Root>
      {/snippet}</Field>
      <Field label="初始现金" id={`${uid}-cash`}>{#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} inputmode="decimal" class="text-right tabular-nums" bind:value={cash} disabled={busy} />{/snippet}</Field>
      <details><summary class="cursor-pointer text-sm text-primary">可选参数（比例用小数）</summary><div class="mt-3 grid gap-3 sm:grid-cols-2">
        {#each fields as [key, label]}
          <Field {label} id={`${uid}-${key}`} hint="留空使用配置值">{#snippet children({ id, describedby })}<Input {id} aria-describedby={describedby} inputmode={key === 'max_positions' ? 'numeric' : 'decimal'} class="text-right tabular-nums" bind:value={params[key]} disabled={busy} />{/snippet}</Field>
        {/each}
      </div></details>
      {#if error}<DataBlock state="error" message={error} />{/if}
      <Dialog.Footer><Button variant="outline" disabled={busy} onclick={() => open = false}>取消</Button><Button type="submit" variant="primary" {busy}>创建模拟账户</Button></Dialog.Footer>
    </form>
  </Dialog.Content>
</Dialog.Root>
