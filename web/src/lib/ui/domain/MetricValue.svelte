<script lang="ts">
  import { cn } from '$lib/utils.js'
  import StatusPill from './StatusPill.svelte'

  // A number with its unit: tabular digits, unit right after (`12 股`, `-3.2%`). `percent` takes a fraction
  // (0.032 -> 3.2%). null / undefined / NaN is 没算, never blank or a dash. `updown`: positive text-up, negative text-down.
  let { value, raw, unit, digits = 2, signed = false, percent = false, tone = 'none' }: {
    value: number | null | undefined; raw?: string | null; unit?: string; digits?: number; signed?: boolean; percent?: boolean; tone?: 'updown' | 'none'
  } = $props()

  // `raw` is for server Decimal strings and preformatted server statistics: never round or parse the displayed text.
  const hasRaw = $derived(raw !== null && raw !== undefined && raw !== '')
  const missing = $derived(!hasRaw && (value === null || value === undefined || !Number.isFinite(value)))
  const shown = $derived(percent ? (value as number) * 100 : (value as number))
  const body = $derived.by(() => {
    if (missing) return ''
    if (hasRaw) return raw!
    const fixed = shown.toFixed(digits)
    if (Number(fixed) === 0) return fixed.replace('-', '')    // never "-0.00"
    return `${signed && shown > 0 ? '+' : ''}${fixed}`
  })
  const suffix = $derived(!hasRaw && percent ? '%' : unit ? ` ${unit}` : '')
  const sign = $derived(missing ? 0 : hasRaw ? Math.sign(Number(raw)) : Math.sign(Number(shown.toFixed(digits))))
</script>

{#if missing}
  <StatusPill domain="block" value="not_computed" />
{:else}
  <span class={cn('break-all tabular-nums', tone === 'updown' && sign > 0 && 'text-up', tone === 'updown' && sign < 0 && 'text-down')}>{body}{suffix}</span>
{/if}
