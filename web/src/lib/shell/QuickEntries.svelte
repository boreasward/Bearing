<script lang="ts">
  import Icon from '$lib/ui/domain/Icon.svelte'
  import type { IconName } from '$lib/ui/domain/icons'
  import { hrefOf, type Route, type ViewName } from '$lib/router'
  import type { TodayCounts } from '$lib/stores/types'
  import { layout } from './layout.svelte'
  let { counts, route, onNavigate }: { counts: TodayCounts | null; route: Route; onNavigate?: () => void } = $props()
  const entries: { view: ViewName; label: string; icon: IconName }[] = [
    { view: 'today', label: '今日', icon: 'calendar-days' },
    { view: 'account', label: '账户', icon: 'wallet' },
    { view: 'market', label: '行情', icon: 'trending-up' },
    { view: 'plans', label: '计划与持仓', icon: 'list' },
    { view: 'research', label: '研究', icon: 'microscope' },
    { view: 'retrospect', label: '复盘', icon: 'history' },
    { view: 'jobs', label: '定时任务', icon: 'clock' },
  ]
  function count(view: ViewName): number | null {
    if (!counts) return null
    if (view === 'today') return counts.actions
    if (view === 'plans') return counts.holdings + counts.pending
    if (view === 'research') return counts.watching
    return null
  }
</script>
<nav aria-label="快捷入口" class="grid gap-1 p-2">
  {#each entries as entry (entry.view)}
    {@const current = route.kind === 'view' && route.view === entry.view}
    {@const badge = count(entry.view)}
    <a href={hrefOf({ kind: 'view', view: entry.view, id: null })} aria-label={entry.label} title={layout.navCollapsed ? entry.label : undefined}
      aria-current={current ? 'page' : undefined} onclick={onNavigate}
      class="relative flex min-h-8 items-center gap-2 rounded-md px-2 text-sm text-foreground hover:bg-muted {current ? 'bg-muted font-semibold' : ''} {layout.navCollapsed ? 'justify-center' : ''}">
      <Icon name={entry.icon} />
      {#if !layout.navCollapsed}<span>{entry.label}</span>{/if}
      {#if badge !== null}
        <span aria-label={`${entry.label} ${badge} 项`} class={layout.navCollapsed ? 'absolute right-1 top-1 size-1 rounded-full bg-primary' : 'ml-auto text-xs tabular-nums text-muted-foreground'}>
          {#if !layout.navCollapsed}{badge}{/if}
        </span>
      {/if}
    </a>
  {/each}
</nav>
