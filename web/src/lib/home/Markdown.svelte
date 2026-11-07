<script lang="ts">
  import * as Table from '$lib/ui/table/index'
  import { parseMarkdown, type Run } from './markdown'

  // Renders markdown.ts blocks with {#each} only — never {@html} (design guard). The brief's
  // `#` heading is a section title under the page header, so levels shift by one (h2…h4).
  let { source }: { source: string } = $props()

  const blocks = $derived(parseMarkdown(source))
</script>

{#snippet runs(runs: Run[])}
  {#each runs as run (run.text)}{#if run.bold}<strong>{run.text}</strong>{:else}{run.text}{/if}{/each}
{/snippet}

<div class="grid gap-2 text-sm">
  {#each blocks as block, i (i)}
    {#if block.type === 'heading'}
      <svelte:element this={`h${Math.min(block.level + 1, 4)}`} class="text-base font-medium">
        {@render runs(block.runs)}
      </svelte:element>
    {:else if block.type === 'paragraph'}
      <p class="whitespace-pre-line">{@render runs(block.runs)}</p>
    {:else if block.type === 'list'}
      <ul class="grid list-disc gap-1 pl-5">
        {#each block.items as item (item.map((run) => run.text).join())}
          <li>{@render runs(item)}</li>
        {/each}
      </ul>
    {:else if block.type === 'table'}
      <Table.Root>
        <Table.Header>
          <Table.Row>
            {#each block.header as cell (cell)}<Table.Head>{cell}</Table.Head>{/each}
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {#each block.rows as row (row.join())}
            <Table.Row>
              {#each row as cell (cell)}<Table.Cell>{cell}</Table.Cell>{/each}
            </Table.Row>
          {/each}
        </Table.Body>
      </Table.Root>
    {/if}
  {/each}
</div>
