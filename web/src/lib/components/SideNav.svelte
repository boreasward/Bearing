<script lang="ts">
  import { Button } from '$lib/ui/button/index'
  import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '$lib/ui/collapsible/index'
  import * as Tooltip from '$lib/ui/tooltip/index'
  import Icon from '$lib/ui/domain/Icon.svelte'
  import type { IconName } from '$lib/ui/domain/icons'
  import { cn } from '$lib/utils.js'

  type NavChild = { id: string; label: string; href?: string }
  type NavItem = { id: string; label: string; icon: IconName; children?: NavChild[] }

  // collapsible = false inside the narrow-screen drawer: always the full menu, no collapse button.
  let { current, sub, collapsible = true }: { current: string; sub?: string; collapsible?: boolean } = $props()

  const primary: NavItem[] = [
    { id: 'today', label: '今日', icon: 'calendar-days' },
    { id: 'instrument', label: '标的', icon: 'search' },
    { id: 'account', label: '账户', icon: 'activity' },
    { id: 'research', label: '研究', icon: 'microscope' },
    {
      id: 'review', label: '复盘', icon: 'clipboard-list', children: [
        { id: 'cards', label: '复盘卡' },
        { id: 'shadow', label: '对照组' },
        { id: 'weekly', label: '周度复盘' },
      ],
    },
    { id: 'data', label: '数据', icon: 'database' },
    {
      // 概念浏览页是 #knowledge 本身，没有子路径
      id: 'knowledge', label: '知识', icon: 'book-open', children: [
        { id: 'concepts', label: '概念', href: '#knowledge' },
        { id: 'principles', label: '原理' },
        { id: 'mine', label: '我的知识' },
      ],
    },
    { id: 'roadmap', label: '路线', icon: 'map', children: [
      { id: 'route', label: '路线', href: '#roadmap' }, { id: 'proposals', label: '提案' },
      { id: 'decisions', label: '决策' }, { id: 'evidence', label: '证据' },
    ] },
    {
      id: 'settings', label: '设置', icon: 'settings', children: [
        { id: 'system',    label: '系统' },
        { id: 'watchlist', label: '关注列表' },
        { id: 'discord',   label: '推送配置' },
        { id: 'jobs',      label: '定时任务' },
        { id: 'env',       label: '环境变量' },
        { id: 'features',  label: '功能开关' },
      ],
    },
  ]

  // Shared-base tools remain reachable under 更多.
  const legacy: NavItem[] = [
    { id: 'tools', label: '工具', icon: 'wrench', children: [
      { id: 'tasks', label: '任务中心' }, { id: 'runs', label: '运行记录' }, { id: 'filings', label: 'SEC 文件' },
    ] },
  ]

  const legacyIds = legacy.map(item => item.id)

  // The collapsed state is remembered per browser; a blocked or throwing storage means expanded.
  const COLLAPSED_KEY = 'ui.sidenav.collapsed'
  function readCollapsed(): boolean {
    try { return globalThis.localStorage?.getItem(COLLAPSED_KEY) === '1' } catch { return false }
  }
  let stored = $state(readCollapsed())
  const collapsed = $derived(collapsible && stored)
  function toggleCollapsed() {
    stored = !stored
    try { globalThis.localStorage?.setItem(COLLAPSED_KEY, stored ? '1' : '0') } catch { /* 存储不可用：只在本页生效 */ }
  }

  let openIds = $state<Set<string>>(new Set<string>())
  let moreOpen = $state(false)

  $effect(() => {
    if (legacyIds.includes(current)) moreOpen = true
  })

  // Keep the active parent expanded whenever current changes
  $effect(() => {
    if (!openIds.has(current)) openIds = new Set([...openIds, current])
  })

  function setOpen(id: string, open: boolean) {
    const next = new Set(openIds)
    if (open) next.add(id)
    else next.delete(id)
    openIds = next
    // also navigate to the zone
    location.hash = id
  }

  function isActive(item: NavItem): boolean {
    return item.id === current
  }

  // 概念页是知识区的默认页：没有子路径（或 concepts / 具体的卡）时都算「概念」；mine 与 mine/… 是「我的知识」。
  function isChildCurrent(item: NavItem, child: NavChild): boolean {
    if (item.id !== current) return false
    if (item.id === 'roadmap') {
      if (child.id === 'route') return !sub || sub === 'route' || sub.startsWith('versions/')
      return sub === child.id || !!sub?.startsWith(`${child.id}/`)
    }
    if (item.id === 'knowledge') {
      const mine = sub === 'mine' || !!sub?.startsWith('mine/')
      if (child.id === 'principles') return sub === 'principles'
      if (child.id === 'mine') return mine
      return sub !== 'principles' && !mine
    }
    if (item.id === 'tools' && child.id === 'runs') return sub === 'runs' || !!sub?.startsWith('runs/')   // 运行详情 #tools/runs/<id>
    return sub === child.id
  }

  const ROW = 'flex h-9 w-full items-center gap-2 rounded-md px-2 text-sm font-normal transition-colors'
  const rowClass = (active: boolean) =>
    cn(ROW, 'justify-start', collapsed && 'justify-center px-0',
      active ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted hover:text-foreground')
</script>

{#snippet entry(item: NavItem)}
  {@const hasChildren = !!item.children?.length}
  {@const open = openIds.has(item.id)}
  {@const active = isActive(item)}

  {#if hasChildren}
    <Collapsible open={open} onOpenChange={next => setOpen(item.id, next)}>
      {#if collapsed}
        <Tooltip.Root>
          <Tooltip.Trigger>
            {#snippet child({ props: tip })}
              <CollapsibleTrigger>
                {#snippet child({ props })}
                  <Button {...tip} {...props} variant="ghost" data-nav-item aria-label={item.label} class={rowClass(active)}>
                    <Icon name={item.icon} />
                  </Button>
                {/snippet}
              </CollapsibleTrigger>
            {/snippet}
          </Tooltip.Trigger>
          <Tooltip.Content side="right">{item.label}</Tooltip.Content>
        </Tooltip.Root>
      {:else}
        <CollapsibleTrigger>
          {#snippet child({ props })}
            <Button {...props} variant="ghost" data-nav-item class={rowClass(active)}>
              <Icon name={item.icon} />
              <span class="flex-1 truncate text-left">{item.label}</span>
              <Icon name="chevron-right" class={cn('transition-transform', open && 'rotate-90')} />
            </Button>
          {/snippet}
        </CollapsibleTrigger>
        {#if open}
          <!-- forceMount: the {#if} above is the open/closed switch (a Bits content that mounts already open stays hidden) -->
          <CollapsibleContent forceMount class="mb-1 ml-3 flex flex-col gap-0.5 border-l border-border pl-3">
            {#each item.children! as child}
              {@const childActive = isChildCurrent(item, child)}
              <a
                href={child.href ?? `#${item.id}/${child.id}`}
                aria-current={childActive ? 'page' : undefined}
                class={cn('block rounded-md px-2 py-1.5 text-xs transition-colors',
                  childActive ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground hover:bg-muted hover:text-foreground')}
              >
                {child.label}
              </a>
            {/each}
          </CollapsibleContent>
        {/if}
      {/if}
    </Collapsible>
  {:else if collapsed}
    <Tooltip.Root>
      <Tooltip.Trigger>
        {#snippet child({ props })}
          <a {...props} href="#{item.id}" data-nav-item aria-label={item.label} aria-current={active ? 'page' : undefined} class={rowClass(active)}>
            <Icon name={item.icon} />
          </a>
        {/snippet}
      </Tooltip.Trigger>
      <Tooltip.Content side="right">{item.label}</Tooltip.Content>
    </Tooltip.Root>
  {:else}
    <a href="#{item.id}" data-nav-item aria-current={active ? 'page' : undefined} class={rowClass(active)}>
      <Icon name={item.icon} />
      <span class="truncate">{item.label}</span>
    </a>
  {/if}
{/snippet}

<Tooltip.Provider>
  <nav
    aria-label="主导航"
    class={cn('flex h-full flex-col border-r border-border bg-card transition-[width] duration-150', collapsible ? (collapsed ? 'w-14' : 'w-48') : 'w-full')}
  >
    {#if collapsible}
      <Button variant="ghost" size="icon" class="mx-2 mt-2 mb-1 self-end text-muted-foreground"
              aria-label={collapsed ? '展开菜单' : '收起菜单'} onclick={toggleCollapsed}>
        <Icon name={collapsed ? 'panel-left-open' : 'panel-left-close'} />
      </Button>
    {/if}

    <div class="flex flex-1 flex-col gap-0.5 overflow-x-hidden overflow-y-auto px-2 {collapsible ? '' : 'pt-2'}">
      {#each primary as item}{@render entry(item)}{/each}

      <Button variant="ghost" data-nav-item aria-expanded={moreOpen} aria-label={collapsed ? '更多' : undefined}
              class={cn(ROW, 'mt-2 justify-start border-t border-border', collapsed && 'justify-center px-0',
                legacyIds.includes(current) ? 'text-primary' : 'text-muted-foreground hover:bg-muted hover:text-foreground')}
              onclick={() => moreOpen = !moreOpen}>
        <Icon name="chevron-down" class={cn('transition-transform', moreOpen && 'rotate-180')} />
        {#if !collapsed}<span class="flex-1 truncate text-left">更多</span>{/if}
      </Button>
      {#if moreOpen}
        {#each legacy as item}{@render entry(item)}{/each}
      {/if}
    </div>
  </nav>
</Tooltip.Provider>
