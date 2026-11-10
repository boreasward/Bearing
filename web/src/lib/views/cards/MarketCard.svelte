<script lang="ts">
  import * as Card from '$lib/ui/card/index'
  import CardFrame from './CardFrame.svelte'
  import DataTable from '$lib/ui/data-table/DataTable.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import { Button } from '$lib/ui/button/index'
  import { bringToChat, type ContextChip } from '../context'
  import { num } from '../data'
  import type { MarketData, WatchRow } from '$lib/stores/types'
  import type { BriefMarket } from '$lib/home/types'
  import type { CallMeta } from '../registry'
  let { data, meta, full = false, pane = false, brief = false, onBring = bringToChat }: { data: BriefMarket & { watch?: WatchRow[] }; meta?: CallMeta; full?: boolean; pane?: boolean; brief?: boolean; onBring?: (chip: ContextChip) => void } = $props()
  type ETF = MarketData['etf_table'][number]
  const etfs = $derived(pane ? [...(data.etf_table ?? [])].sort((a, b) => (b.rs20 ?? -Infinity) - (a.rs20 ?? -Infinity)) : data.etf_table ?? [])
</script>
{#snippet percentage(row: ETF, key: 'ret20' | 'rs20' | 'rs60')}<MetricValue value={row[key]} percent tone="updown" />{/snippet}
{#snippet ret20(row: ETF)}{@render percentage(row, 'ret20')}{/snippet}
{#snippet rs20(row: ETF)}{@render percentage(row, 'rs20')}{/snippet}
{#snippet rs60(row: ETF)}{@render percentage(row, 'rs60')}{/snippet}
{#snippet etfState(row: ETF)}{#if row.state}<StatusPill domain="etf_state" value={row.state} />{:else}<StatusPill domain="block" value="not_computed" />{/if}{/snippet}
{#snippet close(row: WatchRow)}<MetricValue value={num(row.last_close)} currency />{/snippet}
{#snippet change(row: WatchRow)}<MetricValue value={num(row.change_pct) === null ? null : num(row.change_pct)! / 100} percent tone="updown" />{/snippet}
{#snippet etf(row: ETF)}{row.etf}{#if pane}<Button size="sm" variant="ghost" onclick={() => onBring({ kind: 'ticker', ticker: row.etf, id: null })}>带进对话</Button>{/if}{/snippet}
{#snippet links(row: WatchRow)}
  {#if row.plan_id != null}<a class="underline" href={`#plans/${row.plan_id}`}>计划 #{row.plan_id}</a>{/if}
  {#if row.research_id != null}<a class="underline" href={`#research/${row.research_id}`}>研究 #{row.research_id}</a>{/if}
  <Button size="sm" variant="ghost" onclick={() => onBring({ kind: row.plan_id != null ? 'plan' : row.research_id != null ? 'research' : 'ticker', ticker: row.ticker, id: row.plan_id ?? row.research_id } satisfies ContextChip)}>带进对话</Button>
{/snippet}
{#snippet content()}
  <p class="text-xs text-muted-foreground">交易日 {data.session ?? '没算'}</p>
  {#if brief}
    <div class="grid grid-cols-2 gap-3 text-sm">
      <div>区制 <StatusPill domain="regime" value={data.label} /></div>
      <div>广度<p>50 日 <MetricValue value={data.breadth?.pct_50} percent /></p><p>200 日 <MetricValue value={data.breadth?.pct_200} percent /></p></div>
      <div>52 周新高 / 新低<p>{data.breadth?.new_highs ?? '没算'} / {data.breadth?.new_lows ?? '没算'}</p></div>
      <div>SPY 20 日<p><MetricValue value={data.spy_ret20} percent tone="updown" /></p></div>
    </div>
  {:else if pane}
    <div class="grid gap-2 text-sm">
      <p>区制 <StatusPill domain="regime" value={data.label} /></p>
      <p>52 周新高 / 新低：{data.breadth?.new_highs ?? '没算'} / {data.breadth?.new_lows ?? '没算'}</p>
      <p>50 日线上方占比 <MetricValue value={data.breadth?.pct_50} percent /></p>
    </div>
  {:else}
    <div class="grid grid-cols-2 gap-3 text-sm">
      <div>广度<p>50 日 <MetricValue value={data.breadth?.pct_50} percent />（{data.breadth?.above_50 ?? '没算'}/{data.breadth?.eligible_50 ?? '没算'}）</p><p>200 日 <MetricValue value={data.breadth?.pct_200} percent />（{data.breadth?.above_200 ?? '没算'}/{data.breadth?.eligible_200 ?? '没算'}）</p></div>
      <div>SPY 20 日<p><MetricValue value={data.spy_ret20} percent tone="updown" /></p></div>
      <div>52 周新高 / 新低<p>{data.breadth?.new_highs ?? '没算'} / {data.breadth?.new_lows ?? '没算'}（样本 {data.breadth?.eligible_52w ?? '没算'}）</p></div>
      <div>领涨<p>{etfs.slice(0, 2).map(row => row.etf).join('、') || '没算'}</p></div>
    </div>
  {/if}
  <Section title="板块 ETF" level={4}>
    <DataTable ariaLabel="板块 ETF" columns={[
      { key:'etf', header:'ETF', cell:etf }, { key:'ret20', header:'20 日', align:'num', cell:ret20 },
      { key:'rs20', header:'RS20', align:'num', cell:rs20 }, { key:'rs60', header:'RS60', align:'num', cell:rs60 },
      { key:'state', header:'状态', cell:etfState }
    ]} rows={brief ? data.etf_table ?? [] : full || pane ? etfs : etfs.slice(0, 6)} rowKey={row => row.etf} pageSize={0} />
    {#if !etfs.length}<p class="text-sm text-muted-foreground">没有 ETF 数据</p>{/if}
  </Section>
  {#if full && !pane && !brief}
    <Section title="宏观" level={4}>
      {#each Object.entries(data.macro ?? {}) as [name, series] (name)}<p class="text-sm">{name} <MetricValue value={series?.value} /> · 观测日 {series?.obs_date ?? '没算'} · 20 期变化 <MetricValue value={series?.change} />（起点 {series?.change_from ?? '没算'}）</p>{/each}
      {#if !Object.keys(data.macro ?? {}).length}<p class="text-sm text-muted-foreground">没有宏观数据</p>{/if}
    </Section>
  {/if}
  {#if (full || pane) && !brief}
    <Section title="关注的标的" level={4}>
      <DataTable ariaLabel="关注的标的" columns={[
        { key:'ticker', header:'股票' }, { key:'last_close', header:'最新收盘', align:'num', cell:close },
        { key:'change_pct', header:'当日涨跌', align:'num', cell:change }, { key:'links', header:'关联', cell:links }
      ]} rows={data.watch ?? []} rowKey={row => row.ticker} pageSize={0} />
      {#if !data.watch?.length}<p class="text-sm text-muted-foreground">没有关注的标的</p>{/if}
    </Section>
  {/if}
  {#if brief}<a href="#market" class="text-sm underline">打开完整行情 ›</a>{/if}
{/snippet}
{#if brief}
  <Card.Root size="sm" role="region" aria-label="行情">
    <Card.Header><Card.Title>行情</Card.Title></Card.Header>
    <Card.Content class="grid gap-3">{@render content()}</Card.Content>
  </Card.Root>
{:else}
  <CardFrame title="市场温度" {data} {meta} calcVersion={data.calc_version}>
    {#snippet badges()}{#if data.label && !pane}<StatusPill domain="regime" value={data.label} />{/if}{/snippet}
    {@render content()}
  </CardFrame>
{/if}
