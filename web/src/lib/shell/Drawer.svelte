<script lang="ts">
  import type { Snippet } from 'svelte'
  import { layout, type DrawerPane } from './layout.svelte'
  let { drawerPane }: { drawerPane: Snippet<[pane: DrawerPane]> } = $props()
  const title = { dash: '仪表', market: '行情', jobs: '定时任务', tools: '工具' }
</script>
<aside aria-label="侧览" hidden={layout.drawer === 'closed'} class="h-full w-[288px] shrink-0 overflow-y-auto border-l bg-background">
  {#if layout.drawer !== 'closed'}
    <h2 class="border-b px-3 py-2 font-serif text-lg font-semibold">{title[layout.drawer]}</h2>
    <div class="p-3">{@render drawerPane(layout.drawer)}</div>
  {/if}
</aside>
