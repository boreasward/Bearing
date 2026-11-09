<script lang="ts">
  import DataTable from '$lib/ui/data-table/DataTable.svelte'
  import CardFrame from './CardFrame.svelte'
  import { records, text, type Row } from '../data'
  import type { CallMeta } from '../registry'
  let { data, meta }: { data: unknown; meta: CallMeta } = $props()
  const keys = [['date','日期'],['open','开'],['high','高'],['low','低'],['close','收'],['volume','量']]
</script>
<CardFrame title="日线" {data} {meta} emptyMessage="没有可用的日线">
  <DataTable columns={keys.map(([key, header]) => ({ key, header, align: key === 'date' ? 'left' : 'num', value: (row: Row) => text(row[key]) }))} rows={records(data)} rowKey={row => String(row.date)} />
</CardFrame>
