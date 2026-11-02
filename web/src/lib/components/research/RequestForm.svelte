<script lang="ts">
  import { Button } from '$lib/ui/button/index'
  import { Input } from '$lib/ui/input/index'
  import { Textarea } from '$lib/ui/textarea/index'
  import Field from '$lib/ui/field/Field.svelte'
  import ConfirmDialog from '$lib/ui/confirm-dialog/ConfirmDialog.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import { createResearch } from '$lib/research'
  import { describeError, newRequestId } from '$lib/workbench'

  let { onCreated }: { onCreated: () => void } = $props()
  let ticker = $state(''), question = $state(''), horizon = $state<number | undefined>(undefined)
  let open = $state(false), busy = $state(false), error = $state(''), created = $state('')
  let requestId = ''
  const valid = $derived(!!ticker.trim() && question.length <= 500 && (horizon == null || (Number.isInteger(horizon) && horizon > 0)))
  function prepare() { if (valid) { requestId = newRequestId(); error = ''; open = true } }
  async function submit() {
    if (!valid || busy) return
    busy = true; error = ''
    try {
      const result = await createResearch({ ticker: ticker.trim().toUpperCase(), ...(question.trim() ? { question: question.trim() } : {}), ...(horizon == null ? {} : { horizon_days: horizon }), request_id: requestId })
      created = result.id; open = false; onCreated()
    } catch (e) { error = describeError(e) } finally { busy = false }
  }
</script>

<Section title="新建研究" level={3}>
  <form class="grid gap-3" onsubmit={e => { e.preventDefault(); prepare() }}>
    <Field label="标的" id="research-ticker">{#snippet children({ id })}<Input {id} bind:value={ticker} required placeholder="NVDA" />{/snippet}</Field>
    <Field label="问题" id="research-question" hint="可选，最多 500 字">{#snippet children({ id, describedby })}<Textarea {id} bind:value={question} maxlength={500} aria-describedby={describedby} />{/snippet}</Field>
    <Field label="期限（天）" id="research-horizon" hint="可选，正整数">{#snippet children({ id, describedby })}<Input {id} type="number" min={1} step={1} bind:value={horizon} aria-describedby={describedby} />{/snippet}</Field>
    <div><Button type="submit" variant="primary" disabled={!valid || busy}>提交研究</Button></div>
  </form>
  {#if created}<p role="status">研究已排队 <a class="text-primary underline" href={`#research/${created}`}>查看档案</a></p>{/if}
</Section>
<ConfirmDialog bind:open title="提交研究请求" consequence="每天最多 5 次，token 计入当日总量；占用运行时会排队。只出研究档案，不自动开链。" actionLabel="创建研究档案" {busy} confirmDisabled={!valid} onConfirm={submit}>
  <p>{ticker.trim().toUpperCase()} {question}</p>
  {#if error}<DataBlock state="error" message={error} />{/if}
</ConfirmDialog>
