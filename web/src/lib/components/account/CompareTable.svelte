<script lang="ts">
  import type { CompareView } from '$lib/accounts'
  import { DataTable } from '$lib/ui/data-table/index'
  import type { Column } from '$lib/ui/data-table/types'
  import CalcVersion from '$lib/ui/domain/CalcVersion.svelte'
  import CutoffTime from '$lib/ui/domain/CutoffTime.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'

  let { view, valuationWarnings = {} }: { view: CompareView; valuationWarnings?: Record<string, string[]> } = $props()
  type Row = CompareView['rows'][number]
  const versions = $derived(Array.isArray(view.calc_versions) ? view.calc_versions : Object.values(view.calc_versions ?? {}))
  const columns = $derived<Column<Row>[]>([
    { key: 'label', header: '指标' },
    ...view.columns.map((account, index) => ({ key: account.account_id, header: account.name, value: (row: Row) => {
      if (valuationWarnings[account.account_id]?.length && ['inception_return', 'month_return', 'max_drawdown'].includes(row.key)) return '估值不完整'
      const value = row.values[index]
      return value ? [value.text, value.note].filter(Boolean).join(' · ') || '—' : '—'
    } })),
    { key: 'spy', header: 'SPY', value: row => row.spy ?? '—' },
  ])
</script>
<div class="grid min-w-0 gap-3">
  <div class="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">{#each versions as version}<CalcVersion {version} />{/each}<CutoffTime at={view.cutoff_at} /></div>
  {#each view.columns as account}
    {#if valuationWarnings[account.account_id]?.length}<DataBlock state="degraded" message={`${account.name} · ${valuationWarnings[account.account_id].join('；')}`} />{/if}
  {/each}
  <DataBlock state={view.columns.length ? 'ready' : 'empty'} message="暂无活跃账户可对比">
    <DataTable {columns} rows={view.rows} rowKey={row => row.key} cards="narrow" ariaLabel="账户指标对比" />
  </DataBlock>
  <p class="text-xs text-muted-foreground">同一截止时间、同一口径；各账户首个交易日可能不同，曲线分别归一到首个交易日 = 100。</p>
  <p class="text-xs text-muted-foreground">缺失或沿用旧收盘的日期在曲线中留空；首个交易日权益不可用时不绘制该账户曲线。受影响账户的收益率与最大回撤暂不展示。</p>
</div>
