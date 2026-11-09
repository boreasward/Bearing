<script lang="ts">
  import { market } from '$lib/stores'
  import PageHeader from '$lib/ui/domain/PageHeader.svelte'
  import ResourceBlock from './ResourceBlock.svelte'
  import MarketCard from './cards/MarketCard.svelte'
</script>
<div class="grid gap-5">
  <PageHeader title="行情" level={1}>{#snippet meta()}{market.data?.session ?? '还没有运行记录'}{/snippet}</PageHeader>
  <ResourceBlock resource={market} emptyMessage="还没有运行记录">{#if market.data}<MarketCard data={market.data} full />{/if}</ResourceBlock>
</div>
