<script lang="ts">
  import * as Collapsible from '$lib/ui/collapsible/index'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import { toolsByGroup } from '$lib/views/tool-catalog'

  const groups = toolsByGroup()
  let open = $state<Record<string, boolean>>(Object.fromEntries(groups.map(group => [group.key, true])))
</script>
<div class="grid min-w-0 gap-4 text-sm">
  <p class="text-xs text-muted-foreground">助手在对话里能调用的 {groups.reduce((count, group) => count + group.tools.length, 0)} 个工具</p>
  {#each groups as group (group.key)}
    <Collapsible.Root bind:open={open[group.key]}>
      <Collapsible.Trigger class="text-sm font-medium underline-offset-2 hover:underline">{group.label} · {group.tools.length}</Collapsible.Trigger>
      {#if open[group.key]}
        <Collapsible.Content forceMount class="pt-2">
          <ul class="grid min-w-0 gap-3">
            {#each group.tools as tool (tool.name)}
              <li class="grid min-w-0 gap-1 border-b pb-2 last:border-b-0">
                <div class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                  <span class="font-medium">{tool.label}</span>
                  <code class="break-all text-xs text-muted-foreground">{tool.name}</code>
                  {#if tool.write !== 'none'}<StatusPill domain="tool_write" value={tool.write} />{/if}
                </div>
                <p class="min-w-0 whitespace-normal break-words text-xs text-muted-foreground">{tool.summary}</p>
              </li>
            {/each}
          </ul>
        </Collapsible.Content>
      {/if}
    </Collapsible.Root>
  {/each}
</div>
