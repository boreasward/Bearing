<script lang="ts">
  import type { Snippet } from 'svelte'
  let { aside, children }: { aside: Snippet; children: Snippet } = $props()
</script>
<div class="@container">
  <div class="grid gap-4 @[880px]:grid-cols-[minmax(0,1fr)_320px] @[880px]:items-start">
    <aside aria-label="账户与行情" class="grid gap-4 self-start @[560px]:grid-cols-2 @[880px]:sticky @[880px]:top-4 @[880px]:order-2 @[880px]:max-h-[calc(100dvh-6rem)] @[880px]:grid-cols-1 @[880px]:overflow-y-auto">{@render aside()}</aside>
    <div class="grid min-w-0 gap-4 @[880px]:order-1">{@render children()}</div>
  </div>
</div>
