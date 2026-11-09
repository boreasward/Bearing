<script lang="ts">
  import { Button } from '$lib/ui/button/index'
  import Icon from '$lib/ui/domain/Icon.svelte'
  import { layout, setSegment } from './layout.svelte'
</script>
<div class={layout.navCollapsed ? 'flex flex-col gap-1' : 'flex items-center gap-1'} aria-label="导航分段">
  <Button variant="ghost" size="icon" aria-label="快捷入口" title="快捷入口" aria-pressed={layout.navSegment === 'quick'} class={`${layout.navSegment === 'quick' ? 'bg-muted' : ''} ${layout.navCollapsed ? '' : 'w-auto px-1'}`} onclick={() => setSegment('quick')}>
    <Icon name="layout-grid" />{#if !layout.navCollapsed}<span>快捷入口</span>{/if}
  </Button>
  <Button variant="ghost" size="icon" aria-label="对话" title="对话" aria-pressed={layout.navSegment === 'chats'} class={`${layout.navSegment === 'chats' ? 'bg-muted' : ''} ${layout.navCollapsed ? '' : 'w-auto px-1'}`} onclick={() => setSegment('chats')}>
    <Icon name="message-circle" />{#if !layout.navCollapsed}<span>对话</span>{/if}
  </Button>
</div>
