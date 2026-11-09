<script lang="ts">
  import { untrack } from 'svelte'
  import ToolCard from './ToolCard.svelte'
  import type { Block } from './types'
  let { block }: { block: Extract<Block, { kind: 'tool' }> } = $props()
  let state = $state(untrack(() => ({ ...block })))
</script>
<ToolCard bind:block={state} chatId="c1" agentSession="chat-c1-1" />
