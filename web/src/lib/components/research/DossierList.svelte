<script lang="ts">
  import type { ResearchItem } from '$lib/research'
  import DataTable from '$lib/ui/data-table/DataTable.svelte'
  import type { Column } from '$lib/ui/data-table/types'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import CutoffTime from '$lib/ui/domain/CutoffTime.svelte'
  let { items }: { items: ResearchItem[] } = $props()
  const columns: Column<ResearchItem>[] = [
    { key: 'ticker', header: '标的', cell: ticker }, { key: 'question', header: '问题', value: r => r.question || '未指定问题' },
    { key: 'status', header: '状态', cell: status }, { key: 'latest_summary', header: '最新结论', value: r => r.latest_summary || '尚无结论' },
    { key: 'cutoff_at', header: '证据截止', cell: cutoff }, { key: 'tokens', header: 'token', align: 'num', value: r => r.tokens ?? '尚未消耗' },
  ]
</script>
{#snippet ticker(row: ResearchItem)}<a class="text-primary underline" href={`#research/${row.id}`}>{row.ticker}</a>{/snippet}
{#snippet status(row: ResearchItem)}<StatusPill domain="research_request" value={row.status} />{/snippet}
{#snippet cutoff(row: ResearchItem)}<CutoffTime at={row.cutoff_at} />{/snippet}
<DataTable {columns} rows={items} rowKey={r => r.id} ariaLabel="研究档案" />
