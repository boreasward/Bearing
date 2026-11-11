<script lang="ts">
  import * as Table from '$lib/ui/table/index'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import { moneyText } from '$lib/format'
  import type { RotationGroup, RotationRecord, RotationRetroData } from './types'

  // 复盘页的换仓块（v3.3 spec §9）：每次比较一行（交易日、B、A、结论、执行情况、结论之后第 5 / 10 / 20 个交易日 B 减 A 的差），
  // 其后是汇总。涨跌幅与差值是四位小数的比例字符串，页面乘 100 显示；日线不够的时点写「进行中」；
  // 继续持有且没指明持仓的记录对照列为空；样本低于 min_sample 的组只给次数与「样本不足」。
  let { data }: { data: RotationRetroData } = $props()

  const HORIZONS = [5, 10, 20]

  function cell(record: RotationRecord, horizon: number) {
    return record.compare?.find((item) => item.horizon === horizon) ?? null
  }

  function executionText(record: RotationRecord): string {
    const execution = record.execution
    if (!execution) return ''
    const parts: string[] = []
    if (execution.state === 'done') {
      parts.push(`卖出 ${execution.sold_quantity} 股${execution.sold_session ? `（${execution.sold_session}）` : ''}`)
    } else if (record.sell_quantity) {
      parts.push(`计划卖出 ${record.sell_quantity} 股`)
    }
    parts.push(execution.bought ? `${record.candidate} 已买入` : `${record.candidate} 未买入`)
    return parts.join(' · ')
  }
</script>

{#snippet group(title: string, item: RotationGroup)}
  <p class="flex flex-wrap items-center gap-x-1">
    <span>{title}：{item.sample} 次</span>
    {#if item.enough}
      <span>· 20 日后 B 跑赢 A 占比 <MetricValue value={Number(item.b_win_rate)} percent digits={1} /> · 平均差值 <MetricValue value={Number(item.mean_diff)} percent digits={2} signed /></span>
    {:else}
      <span>·</span><StatusPill domain="block" value="insufficient" />
    {/if}
  </p>
{/snippet}

<section class="grid gap-2" aria-label="换仓比较">
  <h3 class="text-base font-medium">换仓比较</h3>
  <p class="text-xs text-muted-foreground">规则未经验证；样本不足的组不给数</p>
  {#if data.records.length === 0}
    <p class="text-sm text-muted-foreground">还没有换仓比较</p>
  {:else}
    <div role="group" aria-label="换仓汇总" class="grid gap-1 text-sm">
      <p class="flex flex-wrap gap-x-4">
        <span>继续持有 {data.summary.counts.hold} 次</span>
        <span>半仓换 {data.summary.counts.half} 次</span>
        <span>全仓换 {data.summary.counts.full} 次</span>
        <span>已执行 {data.summary.executed} 次</span>
        <span>手续费估计合计 {moneyText(data.summary.fee_total)}</span>
      </p>
      {@render group('换仓（半仓与全仓）', data.summary.switched)}
      {@render group('继续持有', data.summary.held)}
    </div>
    <Table.Root>
      <Table.Header>
        <Table.Row>
          <Table.Head>日期</Table.Head>
          <Table.Head>B（候选）</Table.Head>
          <Table.Head>A（持仓）</Table.Head>
          <Table.Head>结论</Table.Head>
          <Table.Head>执行情况</Table.Head>
          {#each HORIZONS as horizon (horizon)}
            <Table.Head class="text-right">{horizon} 日 B 减 A</Table.Head>
          {/each}
        </Table.Row>
      </Table.Header>
      <Table.Body>
        {#each data.records as record (record.rotation_id)}
          <Table.Row>
            <Table.Cell class="whitespace-nowrap text-xs">{record.session}</Table.Cell>
            <Table.Cell>{record.candidate}</Table.Cell>
            <Table.Cell>{record.holding_ticker ?? ''}</Table.Cell>
            <Table.Cell><StatusPill domain="rotation" value={record.verdict} /></Table.Cell>
            <Table.Cell class="text-xs">
              {#if record.execution}
                <span class="flex flex-wrap items-center gap-1">
                  <StatusPill domain="rotation_state" value={record.execution.state} />
                  <span class="text-muted-foreground">{executionText(record)}</span>
                </span>
              {/if}
            </Table.Cell>
            {#each HORIZONS as horizon (horizon)}
              {@const item = cell(record, horizon)}
              <Table.Cell class="text-right">
                {#if item?.status === 'done'}
                  <MetricValue value={Number(item.diff)} percent digits={2} signed tone="updown" />
                  <span class="block text-xs text-muted-foreground">A {Number((Number(item.a_pct) * 100).toFixed(2))}% · B {Number((Number(item.b_pct) * 100).toFixed(2))}%</span>
                {:else if item}
                  <span class="text-xs text-muted-foreground">进行中</span>
                {/if}
              </Table.Cell>
            {/each}
          </Table.Row>
        {/each}
      </Table.Body>
    </Table.Root>
  {/if}
</section>
