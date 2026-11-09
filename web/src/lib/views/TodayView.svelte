<script lang="ts">
  import PageHeader from '$lib/ui/domain/PageHeader.svelte'
  import TodayActions from '$lib/home/TodayActions.svelte'
  import BriefPanel from '$lib/home/BriefPanel.svelte'
  import AccountStrip from '$lib/home/AccountStrip.svelte'
  import { today, invalidateAll } from '$lib/stores'
  import { actionCompleted } from './context'
  import ResourceBlock from './ResourceBlock.svelte'
  let { session = null }: { session?: string | null } = $props()
  const actions = $derived((today.data?.actions ?? []).map(action => ({ ...action, quantity: Number(action.quantity) })))
  function changed() { invalidateAll(); actionCompleted('已更新今日操作') }
</script>
<div class="grid gap-4 p-4">
  <PageHeader level={1} title="今日" />
  <ResourceBlock resource={today} emptyMessage="暂无今日操作"><TodayActions {actions} onChanged={changed} /></ResourceBlock>
  <BriefPanel {session} />
  <AccountStrip />
</div>
