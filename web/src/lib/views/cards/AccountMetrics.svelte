<script lang="ts">
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import { num, record } from '../data'
  let { data }: { data: unknown } = $props()
  const row = $derived(record(data))
  const ledger = $derived(record(row.ledger))
  const positions = $derived(Array.isArray(ledger.positions) ? ledger.positions as Record<string, unknown>[] : [])
  // v3.3 仓位占比：pct / cap 是比例字符串，页面乘 100；旧数据没有 exposure 键时整行不出现，pct 为 null 是没有资金记录
  const exposure = $derived(row.exposure !== null && typeof row.exposure === 'object' ? record(row.exposure) : null)
  const exposurePct = $derived(exposure ? num(exposure.pct) : null)
  const capText = $derived.by(() => {
    const cap = exposure ? num(exposure.cap) : null
    return cap === null ? '' : `${Number((cap * 100).toFixed(1))}%`
  })
  const unrealized = $derived(ledger.positions !== undefined && positions.every(p => num(p.unrealized_pnl) !== null) ? positions.reduce((sum, p) => sum + num(p.unrealized_pnl)!, 0) : null)
</script>
<dl class="grid grid-cols-2 gap-2 text-sm">
  <dt>现金</dt><dd><MetricValue value={num(ledger.cash)} currency /></dd>
  <dt>总资产</dt><dd><MetricValue value={num(ledger.equity)} currency /></dd>
  <dt>浮动盈亏</dt><dd><MetricValue value={unrealized} currency tone="updown" /></dd>
  <dt>已实现盈亏</dt><dd><MetricValue value={num(ledger.realized_pnl)} currency tone="updown" /></dd>
  <dt>对账差额</dt><dd><MetricValue value={num(record(row.reconciliation).difference)} currency /></dd>
</dl>
{#if exposure}
  <p class="text-sm">
    {#if exposurePct === null}
      仓位占比 —（未提供账户资金）
    {:else}
      仓位占比 <MetricValue value={exposurePct} percent digits={1} />{#if capText}（上限 {capText}）{/if}，剩余额度 <MetricValue value={null} raw={exposure.room == null ? null : String(exposure.room)} currency />
    {/if}
  </p>
  {#if exposure.incomplete === true}<p class="text-sm text-warn">有持仓缺日线，占比偏低</p>{/if}
{/if}
