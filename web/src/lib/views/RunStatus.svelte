<script lang="ts">
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import type { RunsData } from '$lib/stores/types'
  let { brief }: { brief: RunsData['runs'][number]['brief'] } = $props()
</script>
<StatusPill domain="daily_run" value={brief?.status ?? 'pending'} />
{#if brief?.unfinished}<StatusPill domain="daily_run" value="unfinished" />{/if}
