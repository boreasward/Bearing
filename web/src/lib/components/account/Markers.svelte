<script lang="ts">
  import { MARKER_KIND_TEXT, type AccountMarker } from '$lib/accounts'
  import Section from '$lib/ui/domain/Section.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import DataTable from '$lib/ui/data-table/DataTable.svelte'
  import type { Column } from '$lib/ui/data-table/types'
  let { markers }: { markers?: AccountMarker[] } = $props()
  const rows = $derived((markers ?? []).map((m, index) => ({ ...m, index })).sort((a, b) => a.session.localeCompare(b.session)))
  const columns: Column<AccountMarker>[] = [{ key: 'session', header: '交易日' }, { key: 'kind', header: '种类', value: r => MARKER_KIND_TEXT[r.kind] }, { key: 'label', header: '分界说明' }]
</script>
<Section title="分界" level={3}>
  <DataBlock state={!markers ? 'not_computed' : rows.length ? 'ready' : 'empty'} message={!markers ? '接口尚未提供分界记录' : '本区间没有功能、版本或参数分界'}>
    <DataTable {columns} {rows} rowKey={r => r.index} ariaLabel="分界记录" />
  </DataBlock>
</Section>
