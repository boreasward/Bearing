<script lang="ts">
  import type { Account } from '$lib/accounts'
  import { parameterSummary, STRATEGY_TEXT } from '$lib/accounts'
  import { Button } from '$lib/ui/button/index'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import { cn } from '$lib/utils'

  let { accounts, selectedId, onnew }: { accounts: Account[]; selectedId: string; onnew: () => void } = $props()
</script>

<div class="flex min-w-0 flex-wrap items-end gap-3">
  <nav aria-label="账户切换" class="flex min-w-0 flex-1 gap-1 overflow-x-auto border-b border-border">
    {#each accounts as account (account.id)}
      <a href={`#account/${account.id}`} aria-current={selectedId === account.id ? 'page' : undefined}
         class={cn('grid shrink-0 gap-1 border-b-2 px-3 py-2 text-sm', selectedId === account.id ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground')}>
        <span class="flex items-center gap-2 font-medium">{account.name}
          {#if !account.opened}<span class="text-xs text-muted-foreground">未开账</span>{/if}
          {#if account.status === 'closed'}<StatusPill domain="account" value="closed" />{/if}
        </span>
        <span class="text-xs text-muted-foreground">{STRATEGY_TEXT[account.strategy]} · {parameterSummary(account.params)}</span>
      </a>
    {/each}
  </nav>
  <Button variant="outline" onclick={onnew}>新建模拟账户</Button>
</div>
