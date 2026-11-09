<script lang="ts">
  import type { Snippet } from 'svelte'

  // Title + one secondary line (calculation version / time point / version) + one action area on the right
  // (at most one `primary` Button in it). Migrations keep each heading's existing level.
  let { title, level = 2, meta, actions }: { title: string; level?: 1 | 2 | 3; meta?: Snippet; actions?: Snippet } = $props()

  const SIZE = { 1: 'text-xl', 2: 'text-lg', 3: 'text-base' } as const
</script>

<header class="flex flex-wrap items-start justify-between gap-x-4 gap-y-2" data-slot="page-header">
  <div class="grid min-w-0 gap-1">
    <svelte:element this={`h${level}`} class="{SIZE[level]} font-serif font-semibold">{title}</svelte:element>
    {#if meta}<div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground" data-slot="page-header-meta">{@render meta()}</div>{/if}
  </div>
  {#if actions}<div class="flex flex-wrap items-center gap-2" data-slot="page-header-actions">{@render actions()}</div>{/if}
</header>
