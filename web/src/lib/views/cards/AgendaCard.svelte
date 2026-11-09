<script lang="ts">
  import Section from '$lib/ui/domain/Section.svelte'
  import CardFrame from './CardFrame.svelte'
  import { record, records, text } from '../data'
  import type { CallMeta } from '../registry'
  let { data, meta }: { data: unknown; meta: CallMeta } = $props()
  const groups = [['holdings','持仓复查'],['relisted','再次上榜'],['confirmations','确认队列'],['research','研究复看']]
</script>
<CardFrame title="待办清单" {data} {meta}>
  {#each groups as [key, title] (key)}
    <Section {title} level={4}>
      {#if records(record(data)[key]).length}<ul class="grid gap-1 text-sm">{#each records(record(data)[key]) as row, index (index)}<li>{text(row.ticker)} · {row.done ? '已完成' : '待处理'}{#if row.plan_id != null} · 计划 #{text(row.plan_id)}{/if}{#if row.rank != null} · 排名 {text(row.rank)}{/if}</li>{/each}</ul>{:else}<p class="text-sm text-muted-foreground">无</p>{/if}
    </Section>
  {/each}
</CardFrame>
