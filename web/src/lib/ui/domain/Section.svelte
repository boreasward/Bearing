<script lang="ts">
  import type { Snippet } from 'svelte'

  // A titled block of a page (no more than two levels deep); the title is optional, the level is the heading's.
  let { title, level = 3, description, actions, children }:
    { title?: string; level?: 2 | 3 | 4; description?: string; actions?: Snippet; children: Snippet } = $props()

  const uid = $props.id()
  const SIZE = { 2: 'text-lg', 3: 'text-base', 4: 'text-sm' } as const
</script>

<section class="grid gap-3" data-slot="section" aria-labelledby={title ? `${uid}-title` : undefined}>
  {#if title || actions}
    <div class="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
      <div class="grid gap-0.5">
        {#if title}<svelte:element this={`h${level}`} id={`${uid}-title`} class="{SIZE[level]} font-serif font-semibold">{title}</svelte:element>{/if}
        {#if description}<p class="text-xs text-muted-foreground">{description}</p>{/if}
      </div>
      {#if actions}<div class="flex flex-wrap items-center gap-2">{@render actions()}</div>{/if}
    </div>
  {:else if description}
    <p class="text-xs text-muted-foreground">{description}</p>
  {/if}
  {@render children()}
</section>
