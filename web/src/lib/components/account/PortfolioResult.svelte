<script lang="ts">
  import { getPortfolioCheck } from '$lib/accounts'
  import { describeError, type PortfolioCheckView } from '$lib/workbench'
  import PortfolioCheck from '$lib/components/today/PortfolioCheck.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import { Button } from '$lib/ui/button/index'
  let { accountId, name, session }: { accountId: string; name: string; session: string | null } = $props()
  let open = $state(false), busy = $state(false), error = $state(''), check = $state<PortfolioCheckView | null>(null)
  let generation = 0
  $effect(() => { accountId; session; open = false; check = null; error = ''; generation++; return () => { generation++ } })
  async function load() {
    if (!session || busy) return
    const seq = ++generation; busy = true; error = ''
    try { const next = await getPortfolioCheck(accountId, session); if (seq === generation) check = next }
    catch (e) { if (seq === generation) error = describeError(e) } finally { if (seq === generation) busy = false }
  }
</script>
<Button variant="outline" size="sm" aria-expanded={open} onclick={() => { open = !open; if (open && !check) load() }}>查看{name}组合结果</Button>
{#if open}<DataBlock state={!session ? 'not_computed' : busy ? 'loading' : error ? 'error' : 'ready'} message={error || '运行没有交易日，无法读取组合结果'} onRetry={load}>
  {#if check}<PortfolioCheck {check} />{/if}
</DataBlock>{/if}
