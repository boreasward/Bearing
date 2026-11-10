<script lang="ts">
  import type { Snippet } from 'svelte'
  import * as Card from '$lib/ui/card/index'
  let { title, tag, actions, children }: { title: string; tag?: string; actions?: Snippet; children: Snippet } = $props()
</script>
<Card.Root size="sm" role="region" aria-label={title}>
  <Card.Header>
    <Card.Title class="flex flex-wrap items-baseline gap-2">{title}{#if tag}<span class="text-xs font-normal text-muted-foreground">{tag}</span>{/if}</Card.Title>
    {#if actions}<Card.Action>{@render actions()}</Card.Action>{/if}
  </Card.Header>
  <Card.Content class="grid gap-3">{@render children()}</Card.Content>
</Card.Root>
