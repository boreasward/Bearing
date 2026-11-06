<script lang="ts">
  import StatusPill from './StatusPill.svelte'

  // The standard one-line price levels, as PlanHistoryTable / PlanTimeline / RuleBaseline print them today:
  // `入场 95.10–97.30 · 止损 91.40 · T1 104.70`, plus ` · 建议 120 股` when shares are known (PlanRowDetail).
  // A level that was not computed says 没算; all four missing is one not-computed badge.
  // `lead` is the word before the entry range (default 入场; e.g. 区间限价);
  // `sharesLabel` the word before the shares (default 建议; an empty string prints just `120 股`).
  let { low, high, stop, target1, shares = null, lead = '入场', sharesLabel = '建议' }: {
    low: number | null; high: number | null; stop: number | null; target1: number | null; shares?: number | null
    lead?: string; sharesLabel?: string
  } = $props()

  const price = (n: number | null) => (n === null || n === undefined || !Number.isFinite(n) ? null : n.toFixed(2))
  const none = $derived([low, high, stop, target1].every(v => price(v) === null))
</script>

{#snippet level(n: number | null)}{#if price(n) === null}<span class="text-warn">没算</span>{:else}<span class="tabular-nums">{price(n)}</span>{/if}{/snippet}

{#if none}
  <StatusPill domain="block" value="not_computed" />
{:else}
  <span class="inline">{lead} {#if price(low) === null && price(high) === null}{@render level(null)}{:else}{@render level(low)}–{@render level(high)}{/if} · 止损 {@render level(stop)} · T1 {@render level(target1)}{#if shares != null}{' '}· {#if sharesLabel}{sharesLabel}{' '}{/if}<span class="tabular-nums">{shares}</span> 股{/if}</span>
{/if}
