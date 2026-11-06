<script lang="ts">
  import PageHeader from '$lib/ui/domain/PageHeader.svelte'
  import * as Card from '$lib/ui/card/index'
  import { Button } from '$lib/ui/button/index'
</script>

<div class="mx-auto grid max-w-2xl gap-4 py-4">
  <PageHeader title="投资助手" level={1} />
  <Card.Root>
    <Card.Content class="grid gap-3">
      <p>今日简报与对话在 v3 第 3 步实现</p>
      <Button variant="link" href="#_ui" class="w-fit">组件画廊</Button>
    </Card.Content>
  </Card.Root>
</div>
