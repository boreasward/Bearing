<script lang="ts">
  import { Button } from '$lib/ui/button/index'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import Field from '$lib/ui/field/Field.svelte'
  import { Input } from '$lib/ui/input/index'
  import * as Table from '$lib/ui/table/index'
  import * as api from '../api'
  import { fmtBeijing, fmtPct } from '../format'
  import { extraEvidence, fetchTarget, noteText, type BrowseResult, type FamilyCard } from '../data-families'
  import CodeText from './CodeText.svelte'
  import ConceptTerm from './ConceptTerm.svelte'
  import { ATTACH } from '../knowledge'

  let { family, targets, onfetched, pageEvidence = '' }: {
    family: FamilyCard
    targets: string[]
    onfetched?: () => void | Promise<void>
    pageEvidence?: string   // the page banner; only the family's supplement is repeated here
  } = $props()
  let evidence = $derived(extraEvidence(family.evidence, pageEvidence))

  let ticker = $state('')
  let draft = $state('')
  let series = $state('BAMLH0A0HYM2')
  let result = $state<BrowseResult | null>(null)
  let loading = $state(false)
  let error = $state('')
  let busy = $state(false)
  let fetchError = $state('')
  let fetchNote = $state('')
  let fetchOrigin = $state<{ family: FamilyCard['id']; ticker: string } | null>(null)
  let generation = 0
  const SELECT = 'h-8 rounded-md border border-input bg-card px-2 text-sm text-foreground'
  let byTicker = $derived(family.id !== 'fred' && family.id !== 'universe')
  let showFetchResult = $derived(fetchOrigin?.family === family.id && fetchOrigin?.ticker === ticker)

  $effect(() => { if (!ticker && targets.length) ticker = targets[0] })
  $effect(() => { draft = ticker })

  async function load() {
    const id = family.id
    const params = id === 'fred' ? { series } : id === 'universe' ? {} : { ticker }
    const token = ++generation
    result = null
    error = ''
    if (id !== 'fred' && id !== 'universe' && !ticker) { loading = false; return }
    loading = true
    try {
      const next = await api.browseData(id, params)
      if (token === generation) result = next
    } catch (e) {
      if (token === generation) error = String((e as Error)?.message ?? e)
    } finally {
      if (token === generation) loading = false
    }
  }

  $effect(() => {
    // A refreshed card also refreshes raw values, without resetting the selection.
    family; ticker; series
    load()
    return () => { generation++ }
  })

  function selectTicker(event: SubmitEvent) {
    event.preventDefault()
    draft = draft.trim().toUpperCase()
    ticker = draft
  }

  async function fetchOne() {
    const target = draft.trim().toUpperCase()
    if (busy || !family.per_ticker || !target) return
    const fetchId = fetchTarget(family)
    // Keep asynchronous outcomes attached to the family/ticker that started the run.
    fetchOrigin = { family: family.id, ticker: target }
    ticker = target
    draft = target
    busy = true
    fetchError = ''
    fetchNote = ''
    try {
      const pending = await api.fetchDataFamily(fetchId, target)
      const run = await api.pollRun(pending.run_id, { timeoutMs: 900_000 })
      const outcome = run.result as {
        status?: string; data_status?: { error?: string | null }
        row?: { failures?: { target: string; reason: string }[] }
      } | undefined
      const messages: string[] = []
      if (run.status === 'error') messages.push(run.error ?? '运行失败')
      else if (outcome?.status === 'busy') messages.push('该族正在采集')
      else if (outcome?.status === 'partial' || outcome?.status === 'failed') {
        messages.push(outcome.status === 'partial' ? '部分失败' : '拉取失败')
        for (const failure of outcome.row?.failures ?? []) messages.push(`${failure.target}：${failure.reason}`)
      }
      if (outcome?.data_status?.error) messages.push(`采集记录写入失败：${outcome.data_status.error}`)
      fetchError = messages.join('；')
      if (!fetchError) fetchNote = '拉取完成'
      await onfetched?.()
      await load()
    } catch (e) {
      const message = String((e as Error)?.message ?? e)
      if (message === 'run timeout') fetchNote = '仍在后台运行，稍后刷新'
      else fetchError = [fetchError, message].filter(Boolean).join('；')
    } finally {
      busy = false
    }
  }

  type Row = Record<string, unknown>
  type Column = { key: string; label: string; format?: (value: unknown) => string; link?: string; concept?: string }
  const text = (value: unknown): string => value == null ? '—' : Array.isArray(value) ? value.join(', ') : typeof value === 'object' ? JSON.stringify(value) : String(value)
  const columns: Record<string, Column[]> = {
    bars: [
      { key: 'price_date', label: '日期' }, { key: 'open', label: '开' }, { key: 'high', label: '高' },
      { key: 'low', label: '低' }, { key: 'close', label: '收' }, { key: 'adj_close', label: 'adj_close' },
      { key: 'volume', label: '成交量' }, { key: 'split_factor', label: '拆股因子' },
      { key: 'revisions', label: '修订次数' }, { key: 'fetched_at', label: '采集时间', format: fmtBeijing },
      { key: 'source', label: '来源' },
    ],
    atr: [
      { key: 'price_date', label: '日期' }, { key: 'close', label: 'close' },
      { key: 'atr', label: 'atr', concept: ATTACH.data.atr },
      { key: 'atr_pct', label: 'atr_pct（ATR/收盘）', format: value => fmtPct(value as number | null), concept: ATTACH.data.atr_pct },
    ],
    news: [
      { key: 'title', label: '标题 / 链接', link: 'url' }, { key: 'source', label: '来源' },
      { key: 'publisher', label: '发布者' }, { key: 'published_at', label: '发布时间', format: fmtBeijing },
    ],
    sec_filings: [
      { key: 'ticker', label: '标的' }, { key: 'form', label: '类型' }, { key: 'filing_date', label: '申报日期' },
      { key: 'accession_number', label: '申报编号' }, { key: 'primary_document', label: '主文档' },
      { key: 'accepted_at', label: '接收时间', format: fmtBeijing }, { key: 'items', label: '条目' },
    ],
    sec_8k: [
      { key: 'title', label: '标题 / 链接', link: 'url' }, { key: 'publisher', label: '发布者' },
      { key: 'published_at', label: '发布时间', format: fmtBeijing },
      { key: 'filing_date_note', label: '申报日期' },
    ],
    fred: [{ key: 'obs_date', label: '观测日期' }, { key: 'value', label: '实际值' }, { key: 'revisions', label: '修订次数' }],
    universe: [{ key: 'ticker', label: '标的' }, { key: 'latest_bar', label: '最近日线日期' }],
    sector: [{ key: 'ticker', label: '标的' }, { key: 'sic', label: 'SIC' }, { key: 'sic_description', label: '行业描述' }, { key: 'group', label: '行业组' }, { key: 'source', label: '来源' }, { key: 'fetched_at', label: '采集时间', format: fmtBeijing }],
    fundamentals: [{ key: 'metric', label: '指标' }, { key: 'value', label: '值' }, { key: 'period', label: '财季' }],
  }
  const tableNames: Record<string, string> = {
    bars: '日线原始值', atr: 'ATR 原始值', news: '资讯原始值', fred: 'FRED 原始值', universe: '范围成员',
    sector: '行业归类', fundamentals: '财季数据', sec_text: 'SEC 原文与事实', earnings: '财报日',
  }
  let rows = $derived(Array.isArray(result?.rows) ? result.rows : [])
</script>

{#snippet rawTable(label: string, values: Row[], fields: Column[])}
  {#if values.length === 0}
    <DataBlock state="empty" message="无记录" />
  {:else}
    <Table.Root aria-label={label}>
      <Table.Header>
        <Table.Row class="hover:bg-transparent">
          {#each fields as column}
            <Table.Head>{#if column.concept}<ConceptTerm id={column.concept}>{column.label}</ConceptTerm>{:else}{column.label}{/if}</Table.Head>
          {/each}
        </Table.Row>
      </Table.Header>
      <Table.Body>
        {#each values as row}
          <Table.Row>
            {#each fields as column}
              <Table.Cell class="whitespace-nowrap">
                {#if column.link && row[column.link]}
                  <a href={String(row[column.link])} target="_blank" rel="noopener noreferrer" class="text-primary underline whitespace-normal wrap-break-word">{text(row[column.key])}</a>
                {:else}{(column.format ?? text)(row[column.key])}{/if}
              </Table.Cell>
            {/each}
          </Table.Row>
        {/each}
      </Table.Body>
    </Table.Root>
  {/if}
{/snippet}

<Section title="原始数据浏览" level={2}>
  {#if evidence}<p class="text-xs text-muted-foreground"><CodeText text={evidence} /></p>{/if}
  {#if byTicker}
    <form onsubmit={selectTicker} class="flex flex-wrap items-end gap-2">
      <Field label="标的" id="browse-ticker">
        {#snippet children({ id })}
          <Input {id} list="browse-targets" bind:value={draft} disabled={busy} class="w-48" placeholder="选择或输入标的" />
        {/snippet}
      </Field>
      <datalist id="browse-targets">{#each targets as target}<option value={target}></option>{/each}</datalist>
      <Button type="submit" size="sm" disabled={busy}>浏览</Button>
      {#if family.per_ticker}
        <Button size="sm" {busy} busyText="采集中…" disabled={!draft.trim()} onclick={fetchOne}>只拉这一只</Button>
      {/if}
    </form>
  {:else if family.id === 'fred'}
    <div class="flex flex-wrap items-end gap-2">
      <Field label="FRED 序列" id="browse-series">
        {#snippet children({ id })}
          <select {id} bind:value={series} class={SELECT}>
            <option value="BAMLH0A0HYM2">BAMLH0A0HYM2</option><option value="T10Y2Y">T10Y2Y</option>
          </select>
        {/snippet}
      </Field>
      <ConceptTerm id={series === 'T10Y2Y' ? ATTACH.data.T10Y2Y : ATTACH.data.BAMLH0A0HYM2} />
    </div>
  {/if}
  {#if showFetchResult}
    {#if fetchError}<p class="text-xs text-destructive">{fetchError}</p>{/if}
    {#if fetchNote}<p class="text-xs text-muted-foreground">{fetchNote}</p>{/if}
  {/if}
  {#if error}
    <DataBlock state="error" message={error} />
  {:else if loading}
    <DataBlock state="loading" skeleton="table" />
  {:else if result}
    {#each result.notes ?? [] as note}<p class="text-xs text-warn">{noteText(note)}</p>{/each}
    {#if result.current_error}<p class="text-xs text-warn">{result.current_error}</p>{/if}
    {#if family.id === 'bars'}
      <p class="text-xs text-muted-foreground">{text(rows[0]?.note ?? '拆股调整、未分红调整；adj_close 全调整')} <ConceptTerm id={ATTACH.data.barsAdjust} /></p>
    {:else if family.id === 'atr'}
      <p class="text-xs text-muted-foreground"><CodeText text={result.note ?? '现算，与 `plan-1` 同一函数；不入库'} /></p>
    {/if}
    {#if family.id === 'universe' && result.meta}
      <p class="text-xs text-muted-foreground"><ConceptTerm id={ATTACH.data.universeMeta} /></p>
      <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm wrap-break-word">
        {#each ['name', 'as_of', 'source', 'note'] as field}
          <dt class="text-muted-foreground">{field}</dt><dd class="min-w-0">{text(result.meta[field])}</dd>
        {/each}
      </dl>
    {/if}
    {#if !result.has_data}
      <DataBlock state="empty" message="无数据，可点拉取" />
    {:else if family.id === 'sec_filings'}
      <h3 class="text-sm font-medium">filings</h3>
      {@render rawTable('filings', result.filings ?? [], columns.sec_filings)}
      <h3 class="text-sm font-medium">sec_8k（未入 filings）</h3>
      {@render rawTable('sec_8k（未入 filings）', result.sec_8k ?? [], columns.sec_8k)}
    {:else}
      {@render rawTable(tableNames[family.id], rows, columns[family.id] ?? [...new Set(rows.flatMap(row => Object.keys(row)))].map(key => ({ key, label: key })))}
    {/if}
  {:else if byTicker}
    <DataBlock state="empty" message="请选择或输入标的" />
  {/if}
</Section>
