<script lang="ts">
  import { market } from '$lib/stores'
  import ResourceBlock from '$lib/views/ResourceBlock.svelte'
  import MarketCard from '$lib/views/cards/MarketCard.svelte'
  import type { ContextChip } from '$lib/views/context'
  let { onBring }: { onBring(chip: ContextChip): void } = $props()
</script>
<div class="grid gap-3">
  <a class="text-xs text-muted-foreground underline" href="#market">打开完整行情 ›</a>
  <ResourceBlock resource={market} emptyMessage="还没有运行记录">
    {#if market.data}<MarketCard data={market.data} pane {onBring} />{/if}
  </ResourceBlock>
</div>
