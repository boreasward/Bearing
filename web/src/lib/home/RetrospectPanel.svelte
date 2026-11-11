<script lang="ts">
  import * as Select from '$lib/ui/select/index'
  import * as Table from '$lib/ui/table/index'
  import Section from '$lib/ui/domain/Section.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import MetricValue from '$lib/ui/domain/MetricValue.svelte'
  import PlanLevels from '$lib/ui/domain/PlanLevels.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import { Button } from '$lib/ui/button/index'
  import { bringToChat } from '$lib/views/context'
  import { retrospectFor } from '$lib/stores'
  import { moneyText } from '$lib/format'
  import RotationRetro from './RotationRetro.svelte'
  import { type Hypothetical, type RetroRecord, type RuleKey, type RuleStatRow, type WeeklySummary } from './types'

  // 复盘（spec §10 复盘块）：顶部周汇总（Select 切周，本周与之前 8 周）、中间执行复盘表
  // （每份已结束计划一行）、底部规则检验两张表（真实成交 / 假设结果）× 三个分组键。
  // 一次 GET /api/retrospect 全部拿到；空库是「还没有已结束的计划」，不是错误。
  let loadedWeek = $state<string | null>(null) // 最近一次取数的周（null = 本周）
  let selectedWeek = $state('')
  let weeks = $state<{ monday: string; friday: string }[]>([])
  // 「可翻」（spec §10）：记录的 limit 阶梯 20 → 50 → 100（契约上限 100，不扩接口）
  let recordLimit = $state(20)

  const resource = $derived(retrospectFor(loadedWeek, recordLimit))
  const view = $derived(resource.data)
  // 没有已结束的计划但已有换仓比较时仍显示整页（换仓块要能看到）
  const nothingToShow = $derived(Boolean(view?.empty) && !view?.rotations?.records.length)
  const blockState = $derived(resource.state === 'ready' && nothingToShow ? 'empty' : resource.state)
  const message = $derived(resource.error || (nothingToShow ? '还没有已结束的计划' : ''))

  const RECORD_LIMIT_STEPS = [20, 50, 100]
  const RECORD_LIMIT_MAX = 100

  function shiftDay(day: string, days: number): string {
    const d = new Date(`${day}T00:00:00Z`)
    d.setUTCDate(d.getUTCDate() + days)
    return d.toISOString().slice(0, 10)
  }

  $effect(() => {
    if (loadedWeek || !view) return
    selectedWeek = shiftDay(view.weekly.week_start, 4)
    weeks = Array.from({ length: 9 }, (_, i) => {
      const monday = shiftDay(view!.weekly.week_start, -7 * i)
      return { monday, friday: shiftDay(monday, 4) }
    })
  })

  function switchWeek(value: string) {
    // 与最近一次实际取数的周比
    if (!value || value === loadedWeek) return
    loadedWeek = value
  }

  function showMore() {
    const next = RECORD_LIMIT_STEPS.find((step) => step > recordLimit)
    if (next == null) return
    recordLimit = next
  }

  // ---- 执行复盘行 ----
  const EXIT_TEXT: Record<string, string> = {
    trailing_stop: '移动止损',
    initial_stop: '初始止损',
    max_hold: '持有到期',
    thesis_broken: '论点失效',
    manual: '手动',
    rotation: '换仓',
  }
  const NOT_FILLED_TEXT: Record<string, string> = { close_below_stop: '收盘跌破止损', never_in_zone: '价格未进入场区间' }

  // 接口价位是字符串：转成数字交给 PlanLevels（design-system §6.2 的约定），转不了的按缺失
  function num(value: string | number | null | undefined): number | null {
    if (value === null || value === undefined || value === '') return null
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }

  function actualText(record: RetroRecord): string {
    const buy = record.actual.first_buy
    const sell = record.actual.last_sell
    if (!buy && !sell) return '未买入'
    const parts: string[] = []
    if (buy) parts.push(`买 ${moneyText(buy.price)} × ${buy.quantity}（${buy.session}）`)
    if (sell) parts.push(`卖 ${moneyText(sell.price)}（${sell.session}）`)
    return parts.join(' → ')
  }

  function hypotheticalText(h: Hypothetical | null): string {
    if (!h) return ''
    if (h.status === 'not_filled') return `照做未成交：${NOT_FILLED_TEXT[h.reason ?? ''] ?? '价格未进入场区间'}`
    if (h.status === 'unfinished') return `照做进行中，浮动 R ${h.r_multiple ?? ''}`
    const exitKind = EXIT_TEXT[h.exit_reason ?? ''] ?? '离场'
    return `照做会在 ${h.fill_session} 成交于 ${moneyText(h.fill_price)}，${h.exit_session} 以${exitKind} ${moneyText(h.exit_price)} 离场，R ${h.r_multiple ?? ''}`
  }

  // 本周名单一句（spec §5.4）：新上榜 / 掉出 / 再次上榜最多，空项不写
  function listingText(listing: WeeklySummary['listing']): string {
    const parts: string[] = []
    if (listing.new.length) parts.push(`新上榜 ${listing.new.join('、')}`)
    if (listing.dropped.length) parts.push(`掉出 ${listing.dropped.join('、')}`)
    if (listing.most_relisted) parts.push(`再次上榜最多 ${listing.most_relisted}`)
    return parts.join(' · ')
  }

  // ---- 规则检验 ----
  const GROUP_LABELS: [RuleKey, string][] = [['trigger_rules', '触发规则'], ['setup_tier', '形态档位'], ['industry', '行业组']]

  function hasRows(side: Record<RuleKey, RuleStatRow[]>): boolean {
    return GROUP_LABELS.some(([key]) => side[key].length > 0)
  }
</script>

<Section title="复盘" level={2}>
  <DataBlock state={blockState} {message} skeleton="table">
    {#if view}
      <div class="grid gap-4">
        <section class="grid gap-2" aria-label="周汇总">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h3 class="text-base font-medium">周汇总</h3>
            {#if weeks.length}
              <Select.Root type="single" bind:value={selectedWeek} onValueChange={(value) => value && switchWeek(value)}>
                <Select.Trigger aria-label="选择周" size="sm">{selectedWeek || '本周'}</Select.Trigger>
                <Select.Content>
                  {#each weeks as week (week.friday)}
                    <Select.Item value={week.friday} label={`${week.monday}–${week.friday}`} />
                  {/each}
                </Select.Content>
              </Select.Root>
            {/if}
          </div>
          <div class="grid gap-1 text-sm">
            <p class="text-xs text-muted-foreground">{view.weekly.week_start}–{view.weekly.week_end}</p>
            <div class="flex flex-wrap items-center gap-x-4 gap-y-1">
              <span>已结束 {view.weekly.ended} 份</span>
              <span class="text-muted-foreground">照做 {view.weekly.followed} 份 · 有偏差 {view.weekly.deviated} 份</span>
              <span class="flex items-center gap-1">R 合计 <MetricValue value={null} raw={view.weekly.total_r} /></span>
              <span class="flex items-center gap-1">本周已实现盈亏 <MetricValue value={null} raw={view.weekly.realized_pnl} currency tone="updown" /></span>
              <span class="flex items-center gap-1">周初总资产 <MetricValue value={null} raw={view.weekly.equity_start} currency /></span>
              <span class="flex items-center gap-1">周末总资产 <MetricValue value={null} raw={view.weekly.equity_end} currency /></span>
            </div>
            <!-- 审查 P1-4：周报的 §5.4 明细——偏差计数、已平仓各份、未买各行、名单变化 -->
            {#if Object.keys(view.weekly.tag_counts).length || view.weekly.closed.length || view.weekly.not_bought.length || listingText(view.weekly.listing) || view.weekly.rotations?.length}
              <ul class="grid gap-1 text-xs text-muted-foreground">
                {#if Object.keys(view.weekly.tag_counts).length}
                  <li>本周偏差：{Object.entries(view.weekly.tag_counts).map(([tag, count]) => `${tag} ×${count}`).join('；')}</li>
                {/if}
                {#each view.weekly.closed as row (row.plan_id)}
                  <li>{row.ticker}：R {row.r_multiple}（{EXIT_TEXT[row.exit_reason ?? ''] ?? row.exit_reason}）{row.tags.length ? ` · ${row.tags.join('、')}` : ''}</li>
                {/each}
                {#each view.weekly.not_bought as row (row.plan_id)}
                  <li>{row.ticker}：{row.end_reason}{row.hypothetical ? `；${hypotheticalText(row.hypothetical)}` : ''}</li>
                {/each}
                {#if listingText(view.weekly.listing)}
                  <li>本周名单：{listingText(view.weekly.listing)}</li>
                {/if}
                {#each view.weekly.rotations ?? [] as row, index (index)}
                  <li>本周换仓：{row.session} {row.candidate} {row.verdict_text}{row.holding_ticker ? `（${row.verdict === 'hold' ? '对照' : '卖出'} ${row.holding_ticker}）` : ''}</li>
                {/each}
              </ul>
            {/if}
          </div>
        </section>

        <section class="grid gap-2" aria-label="执行复盘">
          <h3 class="text-base font-medium">执行复盘</h3>
          <Table.Root>
            <Table.Header>
              <Table.Row>
                <Table.Head>股票</Table.Head>
                <Table.Head>起止</Table.Head>
                <Table.Head>计划说的</Table.Head>
                <Table.Head>实际做的</Table.Head>
                <Table.Head class="text-right">R</Table.Head>
                <Table.Head>标签</Table.Head>
                <Table.Head>假设结果</Table.Head>
              </Table.Row>
            </Table.Header>
            <Table.Body>
              {#each view.records as record (record.plan_id)}
                <Table.Row>
                  <Table.Cell>{record.ticker} <Button size="sm" variant="ghost" onclick={() => bringToChat({ kind:'plan', ticker:record.ticker, id:record.plan_id })}>带进对话</Button></Table.Cell>
                  <Table.Cell class="whitespace-nowrap text-xs">{record.session}{record.ended_session ? `–${record.ended_session}` : ''}</Table.Cell>
                  <Table.Cell>
                    <PlanLevels
                      low={num(record.planned.entry_low)}
                      high={num(record.planned.entry_high)}
                      stop={num(record.planned.initial_stop)}
                      target1={num(record.planned.target1)}
                      shares={record.planned.shares}
                    />
                  </Table.Cell>
                  <Table.Cell class="whitespace-nowrap text-xs">{actualText(record)}</Table.Cell>
                  <Table.Cell class="text-right">
                    {#if record.r_text}
                      <span class="text-xs text-muted-foreground">{record.r_text}</span>
                    {:else}
                      <MetricValue value={null} raw={record.r_multiple} />
                    {/if}
                  </Table.Cell>
                  <Table.Cell>
                    <span class="flex flex-wrap gap-1">
                      {#if record.followed}
                        <StatusPill domain="retro_tag" value="followed" />
                      {:else}
                        {#each record.tags as tag (tag.code)}
                          <StatusPill domain="retro_tag" value={tag.code} text={tag.text} />
                        {/each}
                      {/if}
                    </span>
                  </Table.Cell>
                  <Table.Cell class="text-xs">{hypotheticalText(record.hypothetical)}</Table.Cell>
                </Table.Row>
              {/each}
            </Table.Body>
          </Table.Root>
          {#if view.records.length >= recordLimit && recordLimit < RECORD_LIMIT_MAX}
            <div>
              <Button size="sm" variant="outline" onclick={showMore}>显示更多</Button>
            </div>
          {/if}
        </section>

        <section class="grid gap-2" aria-label="规则检验">
          <h3 class="text-base font-medium">规则检验</h3>
          <p class="text-xs text-muted-foreground">{view.note}</p>
          {#snippet ruleSide(title: string, side: Record<RuleKey, RuleStatRow[]>)}
            <div class="grid gap-2">
              <h4 class="text-sm font-medium">{title}</h4>
              {#if hasRows(side)}
                {#each GROUP_LABELS as [key, label] (key)}
                  {#if side[key].length}
                    <p class="text-xs text-muted-foreground">{label}</p>
                    <Table.Root>
                      <Table.Header>
                        <Table.Row>
                          <Table.Head>组</Table.Head>
                          <Table.Head class="text-right">样本</Table.Head>
                          <Table.Head class="text-right">胜率</Table.Head>
                          <Table.Head class="text-right">平均 R</Table.Head>
                          <Table.Head class="text-right">R 合计</Table.Head>
                        </Table.Row>
                      </Table.Header>
                      <Table.Body>
                        {#each side[key] as row (row.group)}
                          <Table.Row>
                            <Table.Cell>{row.group}</Table.Cell>
                            <Table.Cell class="text-right tabular-nums">{row.sample}</Table.Cell>
                            <Table.Cell class="text-right">
                              {#if row.insufficient}
                                <StatusPill domain="block" value="insufficient" />
                              {:else}
                                <MetricValue value={Number(row.win_rate)} percent digits={1} />
                              {/if}
                            </Table.Cell>
                            <Table.Cell class="text-right">
                              {#if !row.insufficient}<MetricValue value={Number(row.avg_r)} digits={2} />{/if}
                            </Table.Cell>
                            <Table.Cell class="text-right">
                              {#if !row.insufficient}<MetricValue value={Number(row.total_r)} digits={2} />{/if}
                            </Table.Cell>
                          </Table.Row>
                        {/each}
                      </Table.Body>
                    </Table.Root>
                  {/if}
                {/each}
              {:else}
                <p class="text-xs text-muted-foreground">暂无</p>
              {/if}
            </div>
          {/snippet}
          {@render ruleSide('真实成交', view.rules.realized)}
          {@render ruleSide('假设结果', view.rules.hypothetical)}
        </section>

        {#if view.rotations}<RotationRetro data={view.rotations} />{/if}
      </div>
    {/if}
  </DataBlock>
</Section>
