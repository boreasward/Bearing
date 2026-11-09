<script lang="ts">
  import { Button } from '$lib/ui/button/index'
  let { onNewChat, disabled = false }: { onNewChat(): void; disabled?: boolean } = $props()
</script>
<div class="grid gap-3 py-8">
  <h1 class="font-serif text-xl font-semibold">新开对话</h1>
  <p class="text-sm text-muted-foreground">从市场、持仓或一份计划开始提问。</p>
  <div><Button variant="outline" {disabled} onclick={onNewChat}>新开对话</Button></div>
</div>
