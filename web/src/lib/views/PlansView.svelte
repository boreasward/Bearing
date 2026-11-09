<script lang="ts">
  import PageHeader from '$lib/ui/domain/PageHeader.svelte'
  import PlanCards from '$lib/home/PlanCards.svelte'
  let { id = null }: { id?: number | null } = $props()
</script>
<div class="grid gap-4 p-4"><PageHeader level={1} title="计划与持仓" /><PlanCards {id} /></div>
