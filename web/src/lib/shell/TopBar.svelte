<script lang="ts">
  import { Button } from '$lib/ui/button/index'
  import Icon from '$lib/ui/domain/Icon.svelte'
  import { layout, type DrawerPane } from './layout.svelte'
  let { title, session, onToggleDrawer, onOpenNav, onOpenSettings }: {
    title: string; session: string | null; onToggleDrawer(pane: DrawerPane, trigger: HTMLElement): void
    onOpenNav(trigger: HTMLElement): void; onOpenSettings(): void
  } = $props()
  const panes = [
    { pane: 'dash', label: '仪表', icon: 'layout-grid' },
    { pane: 'market', label: '行情', icon: 'trending-up' },
    { pane: 'jobs', label: '定时任务', icon: 'clock' },
  ] as const
</script>
<header class="flex flex-wrap items-center justify-between gap-2 border-b bg-background px-3 py-2">
  <div class="flex min-w-0 items-center gap-2">
    {#if layout.narrow}
      <Button variant="ghost" size="icon" aria-label="打开左栏" title="打开左栏" onclick={(event) => onOpenNav(event.currentTarget)}><Icon name="menu" /></Button>
    {/if}
    <h1 class="truncate font-serif text-lg font-semibold">{title}</h1>
    <span class="text-xs text-muted-foreground">{session ? `数据截至 ${session}` : '还没有运行记录'}</span>
  </div>
  <div class="flex items-center gap-1" aria-label="侧览面板">
    {#each panes as item (item.pane)}
      <Button variant="ghost" size="icon" aria-label={item.label} title={item.label} aria-pressed={layout.drawer === item.pane}
        class={layout.drawer === item.pane ? 'bg-muted' : ''} onclick={(event) => onToggleDrawer(item.pane, event.currentTarget)}><Icon name={item.icon} /></Button>
    {/each}
    <span class="mx-1 h-4 border-l" aria-hidden="true"></span>
    <Button variant="ghost" size="icon" aria-label="设置" title="设置" onclick={onOpenSettings}><Icon name="settings" /></Button>
  </div>
</header>
