<script lang="ts">
  import type { Resource } from './resource.svelte'
  let { resource }: { resource: Resource<unknown> } = $props()
</script>
<p data-state={resource.state}>{JSON.stringify(resource.data)}{resource.error}</p>
