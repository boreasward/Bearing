<script lang="ts">
  import { Button } from '$lib/ui/button/index'
  let { onSend, onResearch, disabled = false }: { onSend(text: string): void; onResearch(): void; disabled?: boolean } = $props()
  const prompts = [
    ['昨天市场热度', '昨天的市场热度怎么样？'], ['今日操作', '今天我要做什么？'],
    ['持仓怎么样', '持仓现在什么情况？'], ['待入场名单', '待入场的计划有哪些？'],
  ]
</script>
<div class="flex flex-wrap gap-1" aria-label="快捷提问">
  {#each prompts as [label, text]}<Button size="sm" variant="outline" {disabled} onclick={() => onSend(text)}>{label}</Button>{/each}
  <Button size="sm" variant="outline" {disabled} onclick={onResearch}>发起研究…</Button>
</div>
