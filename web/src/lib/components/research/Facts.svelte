<script lang="ts">
  import { FACT_KIND_TEXT, type FactKind, type ResearchOutput } from '$lib/research'
  import SourceBadge from '$lib/ui/domain/SourceBadge.svelte'
  import { Button } from '$lib/ui/button/index'
  let { output, onRef }: { output: ResearchOutput; onRef?: (ref: string) => void } = $props()
</script>
<div class="grid gap-2">
  <p>{output.summary}</p>
  {#each Object.entries(FACT_KIND_TEXT) as [kind, label]}
    {@const facts = output.facts.filter(f => f.kind === kind as FactKind)}
    {#if facts.length}
      <div><SourceBadge source={label} /><ul class="mt-1 grid gap-1" aria-label={label}>
        {#each facts as fact}<li>{fact.text} {#each fact.refs as ref}{#if onRef}<Button variant="link" size="sm" onclick={() => onRef?.(ref)}>{ref}</Button>{:else}<span class="text-xs text-muted-foreground">{ref} </span>{/if}{/each}</li>{/each}
      </ul></div>
    {/if}
  {/each}
  {#if output.risks.length}<h5 class="text-sm font-medium">研究风险</h5><ul>{#each output.risks as risk}<li>{risk.text} <span class="text-xs text-muted-foreground">{risk.refs.join('、')}</span></li>{/each}</ul>{/if}
  <div><h5 class="text-sm font-medium">缺失资料</h5>
    {#if output.blocked.length}<ul aria-label="缺失资料">{#each output.blocked as item}<li>{item.what}：{item.why}</li>{/each}</ul>{:else}<p class="text-xs text-muted-foreground">未记录资料缺口</p>{/if}
  </div>
</div>
