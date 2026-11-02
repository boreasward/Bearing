<script lang="ts">
  import { Alert, AlertDescription } from '$lib/ui/alert/index'
  import { Button } from '$lib/ui/button/index'
  import * as Card from '$lib/ui/card/index'
  import DataTable from '$lib/ui/data-table/DataTable.svelte'
  import type { Column } from '$lib/ui/data-table/types'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import Icon from '$lib/ui/domain/Icon.svelte'
  import PageHeader from '$lib/ui/domain/PageHeader.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import SourceBadge from '$lib/ui/domain/SourceBadge.svelte'
  import * as api from '../lib/api'
  import { KNOWN_FAMILIES, triggerLabel, type DataOverview, type FamilyDetail, type FetchRun, type IndicatorItem } from '../lib/data-families'
  import { fmtBeijing } from '../lib/format'
  import DataFamilyCard from '../lib/components/DataFamilyCard.svelte'
  import DataBrowse from '../lib/components/DataBrowse.svelte'
  import ConceptTerm from '../lib/components/ConceptTerm.svelte'
  import { ATTACH } from '../lib/knowledge'

  let { sub }: { sub?: string } = $props()

  let overview = $state<DataOverview | null>(null)
  let detail = $state<FamilyDetail | null>(null)
  let error = $state('')
  let loading = $state(true)
  let onlyLagging = $state(false)
  let generation = 0
  let familyId = $derived(sub && (KNOWN_FAMILIES as readonly string[]).includes(sub) ? sub : null)
  let coverageRows = $derived(detail?.rows.filter(row => !onlyLagging || row.lagging) ?? [])
  let targets = $derived(detail?.rows.map(row => row.target) ?? [])
  const cards = $derived([...(overview?.families ?? [])].sort((a, b) => {
    const rank = (id: string) => id === 'indicators' ? 99 : (KNOWN_FAMILIES as readonly string[]).indexOf(id)
    return rank(a.id) - rank(b.id)
  }))
  const indicatorColumns: Column<IndicatorItem>[] = [
    { key: 'title', header: '指标', cell: indicator }, { key: 'basis', header: '口径' },
    { key: 'window', header: '窗口', value: r => r.window ?? '不适用' }, { key: 'used_by', header: '使用方', value: r => r.used_by.join('、') },
  ]

  async function load(id: string | null) {
    const token = ++generation
    try {
      const [next, nextDetail] = await Promise.all([
        api.getDataFamilies(), id ? api.getDataFamily(id) : Promise.resolve(null),
      ])
      if (token !== generation) return
      overview = next
      detail = nextDetail
      error = ''
    } catch (e) {
      if (token === generation) { error = String((e as Error)?.message ?? e); overview = null; detail = null }
    } finally {
      if (token === generation) loading = false
    }
  }

  $effect(() => {
    const id = familyId
    loading = true
    error = ''
    detail = null
    onlyLagging = false
    load(id)
    return () => { generation++ }
  })

  const latestText = (value: string | null): string =>
    value == null ? '无记录' : value.length === 10 ? value : fmtBeijing(value)

  type Coverage = FamilyDetail['rows'][number]
  const runColumns: Column<FetchRun>[] = [
    { key: 'trigger', header: '触发来源', value: r => triggerLabel(r.trigger) },
    { key: 'status', header: '状态', cell: statusCell },
    { key: 'count', header: '成功/目标', value: r => `${r.ok_count}/${r.target_count}` },
    { key: 'scope', header: '范围', value: r => (r.scope === 'ticker' ? r.ticker : '全族') },
    { key: 'job', header: '任务', cell: jobCell },
    { key: 'started', header: '开始时间', value: r => fmtBeijing(r.started_at) },
    { key: 'finished', header: '完成时间', value: r => fmtBeijing(r.finished_at) },
    { key: 'failures', header: '失败原因', cell: failuresCell },
  ]
  const coverageColumns: Column<Coverage>[] = [
    { key: 'target', header: '目标' }, { key: 'latest', header: '最新时点', value: r => latestText(r.latest_at) },
    { key: 'lagging', header: '是否滞后', cell: laggingCell }, { key: 'failure', header: '最近失败原因', cell: lastFailureCell },
    { key: 'source_used', header: '实际来源', cell: sourceCell },
  ]
</script>

{#snippet indicator(row: IndicatorItem)}<a class="text-primary underline" href={`#knowledge/${row.concept}`}>{row.title}</a> <code class="text-xs text-muted-foreground">{row.id}</code>{/snippet}
{#snippet sourceCell(row: Coverage)}{#if row.source_used}<SourceBadge source={row.source_used} fallback={!!row.fallback?.tried} />{:else}<span>未记录来源</span>{/if}{#if row.fallback?.reason}<p>{row.fallback.reason}</p>{/if}{/snippet}

{#snippet statusCell(run: FetchRun)}<StatusPill domain="fetch" value={run.status} text={run.status} />{/snippet}
{#snippet jobCell(run: FetchRun)}{run.job}{#if run.detail?.targets_source}
  <span class="block text-xs text-muted-foreground">{run.detail.targets_source}</span>
{/if}{/snippet}
{#snippet failuresCell(run: FetchRun)}
  <div class="min-w-40 whitespace-normal">
    {#if run.status === 'skipped' && run.detail?.reason}
      <p class="text-warn">跳过原因：{run.detail.reason}</p>
    {/if}
    {#each run.failures as failure}<p class="text-destructive">{failure.target}：{failure.reason}</p>{:else}{#if run.status !== 'skipped'}无失败{/if}{/each}
  </div>
{/snippet}
{#snippet laggingCell(row: Coverage)}<span class={row.lagging ? 'text-warn' : 'text-muted-foreground'}>{row.lagging ? '滞后' : '否'}</span>{/snippet}
{#snippet lastFailureCell(row: Coverage)}<span class="text-destructive">{row.last_failure ?? '无'}</span>{/snippet}

<section class="grid min-w-0 gap-4">
  {#if error}
    <DataBlock state="error" message={error} />
  {:else if loading}
    <DataBlock state="loading" skeleton="block" />
  {:else if overview}
    <PageHeader title={familyId ? `${detail?.card.title ?? familyId} · 数据族` : '数据'} level={1}>
      {#snippet meta()}<span>最近收盘交易日 {overview?.session}</span>{/snippet}
      {#snippet actions()}{#if familyId}<Button variant="link" size="sm" href="#data" class="text-xs">返回总览</Button>{/if}{/snippet}
    </PageHeader>
    <Alert variant="info" aria-label="证据说明">
      <Icon name="info" />
      <AlertDescription class="text-xs">{overview.evidence} <ConceptTerm id={ATTACH.data.evidence} /></AlertDescription>
    </Alert>
    {#if !overview.fetch_runs_ready || (familyId && detail && !detail.card.fetch_runs_ready)}
      <Alert variant="warn">
        <Icon name="triangle-alert" />
        <AlertDescription class="text-xs"><span>迁移 024 未执行</span>：最近拉取不可见，拉取接口不可用。</AlertDescription>
      </Alert>
    {/if}
    {#if familyId && detail}
      <DataFamilyCard card={detail.card} pageEvidence={overview.evidence} onfetched={() => load(familyId)} />
      <Section title="最近 20 次拉取" level={2}>
        {#snippet actions()}<Button variant="link" size="sm" href="#tools/runs" class="text-xs">运行记录</Button>{/snippet}
        {#if detail.runs.length}
          <DataTable columns={runColumns} rows={detail.runs} rowKey={r => r.id} ariaLabel="最近拉取" cards="narrow" />
        {:else}
          <DataBlock state="empty" message="无记录" />
        {/if}
      </Section>
      <Section title="逐目标覆盖" level={2}>
        {#snippet actions()}<label class="flex items-center gap-1 text-xs"><input type="checkbox" bind:checked={onlyLagging} />仅看滞后</label>{/snippet}
        {#if coverageRows.length}
          <DataTable columns={coverageColumns} rows={coverageRows} rowKey={r => r.target} ariaLabel="逐目标覆盖" cards="narrow" />
        {:else}
          <DataBlock state="empty" message={onlyLagging ? '无滞后目标' : '无目标'} />
        {/if}
      </Section>
      {#key familyId}
        <DataBrowse family={detail.card} {targets} pageEvidence={overview.evidence} onfetched={() => load(familyId)} />
      {/key}
    {:else}
      <div class="grid gap-3 md:grid-cols-2">
        {#each cards as card (card.id)}
          {#if card.id === 'indicators'}
            <Card.Root size="sm" role="article" aria-label="指标" class="md:col-span-2"><Card.Content class="grid gap-3">
              <h2 class="text-base font-medium">指标</h2><p class="text-xs text-muted-foreground">派生计算：口径与窗口由注册表定义，数据完整不代表信号有效。</p>
              <DataBlock state={card.items.length ? 'ready' : 'empty'} message="指标注册表为空"><DataTable columns={indicatorColumns} rows={card.items} rowKey={r => r.id} ariaLabel="指标注册表" /></DataBlock>
            </Card.Content></Card.Root>
          {:else}<DataFamilyCard {card} pageEvidence={overview.evidence} onfetched={() => load(familyId)} />{/if}
        {/each}
      </div>
    {/if}
  {/if}
</section>
