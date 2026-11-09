<script lang="ts">
  import type { CallMeta } from '../registry'
  import { fmtBeijing } from '$lib/format'
  let { meta, calcVersion }: { meta: CallMeta; calcVersion?: string | null } = $props()
</script>
<p class="font-mono text-xs text-muted-foreground" data-slot="source-line">{meta.tool} · {meta.ref ? `${meta.ref} · ${meta.session ?? (meta.calledAt ? fmtBeijing(meta.calledAt) : '时点未记录')}` : '调用中'}{#if calcVersion}{` · ${calcVersion}`}{/if}</p>
