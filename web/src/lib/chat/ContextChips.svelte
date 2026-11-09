<script lang="ts">
  import { Button } from '$lib/ui/button/index'
  import { Badge } from '$lib/ui/badge/index'
  import { formatPrefix, type ContextChip } from './context'
  let { chips, onRemove }: { chips: ContextChip[]; onRemove?: (index: number) => void } = $props()
</script>
<div class="flex flex-wrap gap-1">
  {#each chips as chip, index}
    {@const label = formatPrefix([chip]).slice(1, -2)}
    {#if onRemove}
      <Button size="sm" variant="outline" aria-label={`移除 ${label}`} onclick={() => onRemove?.(index)}>{label} ×</Button>
    {:else}<Badge>{label}</Badge>{/if}
  {/each}
</div>
