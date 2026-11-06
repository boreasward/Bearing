<script lang="ts">
  // Test fixture. `gated`: the content sits in an {#if open} (a pattern the pages used), so it mounts in the
  // same update that opens the root.
  import { Collapsible, CollapsibleContent } from './index'
  let { open = true, gated = false, force = false }: { open?: boolean; gated?: boolean; force?: boolean } = $props()
</script>

<Collapsible {open}>
  {#if !gated || open}<CollapsibleContent forceMount={force}><p>内容</p></CollapsibleContent>{/if}
</Collapsible>
