<script lang="ts">
  import PageHeader from '$lib/ui/domain/PageHeader.svelte'
  import ResearchCards from '$lib/home/ResearchCards.svelte'
  let { id = null, onStartResearch }: { id?: number | null; onStartResearch(text: string): void } = $props()
</script>
<div class="grid gap-4 p-4"><PageHeader level={1} title="研究" /><ResearchCards {id} {onStartResearch} /></div>
