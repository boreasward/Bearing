<script lang="ts">
  import { Button } from '$lib/ui/button/index'

  // One reference of a list: fact: calls back with the key (page scrolls to it), call: toggles that tool call
  // (only when the caller can expand it: `onCall` given), anything else is plain text. The list wrapper and the
  // expanded call region stay with the caller.
  let { ref, onFact, onCall, expanded = false }: {
    ref: string; onFact?: (key: string) => void; onCall?: (ref: string) => void; expanded?: boolean
  } = $props()
</script>

{#if ref.startsWith('fact:') && onFact}
  <Button variant="link" size="sm" class="select-text text-[length:inherit] font-normal" onclick={() => onFact?.(ref.slice('fact:'.length))}>{ref}</Button>
{:else if ref.startsWith('call:') && onCall}
  <Button variant="link" size="sm" class="select-text text-[length:inherit] font-normal" aria-expanded={expanded} onclick={() => onCall?.(ref)}>{ref}</Button>
{:else}
  <span>{ref}</span>
{/if}
