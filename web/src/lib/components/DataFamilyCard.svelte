<script lang="ts">
  import { Button } from '$lib/ui/button/index'
  import * as Card from '$lib/ui/card/index'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import SourceBadge from '$lib/ui/domain/SourceBadge.svelte'
  import * as api from '../api'
  import CodeText from './CodeText.svelte'
  import ConceptTerm from './ConceptTerm.svelte'
  import { familyConcept } from '../knowledge'
  import { fmtBeijing } from '../format'
  import {
    cardNotes, extraEvidence, fetchButtonLabel, fetchTarget, kindLabel, noteText, triggerLabel,
    type FamilyCard, type LastRun,
  } from '../data-families'

  // pageEvidence: the banner the page already shows; the card then adds only its own supplement.
  let { card, onfetched, pageEvidence = '' }: {
    card: FamilyCard; onfetched: () => void; pageEvidence?: string
  } = $props()
  let evidence = $derived(extraEvidence(card.evidence, pageEvidence))
  let notes = $derived(cardNotes(card))

  let busy = $state(false)
  let error = $state('')
  let note = $state('')

  // 全族拉取：提交后走现有 run 轮询；15 分钟上限，超时不算失败（后台仍在跑）。
  async function fetchAll() {
    if (busy) return
    busy = true
    error = ''
    note = ''
    try {
      const pending = await api.fetchDataFamily(fetchTarget(card), undefined)
      const run = await api.pollRun(pending.run_id, { timeoutMs: 900_000 })
      const result = run.result as { status?: string; data_status?: { error?: string | null } } | undefined
      if (result?.status === 'busy') {
        error = '该族正在采集'   // 探测后、持锁前被批处理抢到
      } else if (run.status === 'error') {
        error = run.error ?? '运行失败'
      } else if (result?.data_status?.error) {
        error = `采集记录写入失败：${result.data_status.error}`
      }
      await onfetched()
    } catch (e) {
      const message = String((e as Error)?.message ?? e)
      if (message === 'run timeout') note = '仍在后台运行，稍后刷新'
      else error = message
    } finally {
      busy = false
    }
  }

  // 最新时点是纯日期（日线 / 申报 / FRED）时直接显示，不当成午夜时间戳
  const latestText = (value: string | null): string =>
    value == null ? '无记录' : value.length === 10 ? value : fmtBeijing(value)
</script>

<!-- 主行只看全族记录；比它更新的单只拉取另起一行，下一次全族拉取后收起 -->
{#snippet runLine(label: string, run: LastRun, single: boolean)}
  <div aria-label={label} class="grid gap-1">
    <p class="flex flex-wrap items-center gap-1.5 {single ? 'text-xs' : ''}">
      {label} <StatusPill domain="fetch" value={run.status} text={run.status} />
      {#if single}· <span>{run.ticker}</span>{/if}
      · <span>{triggerLabel(run.trigger)}</span>
      · <span>{run.job}</span> · <span>{fmtBeijing(run.finished_at)}</span>
      · <span>{run.ok_count}/{run.target_count}</span>
    </p>
    {#if run.status === 'skipped' && run.reason}
      <p class="text-xs text-warn">跳过原因：{run.reason}</p>
    {/if}
    {#if run.failures.length}
      <ul class="grid gap-0.5 text-xs text-destructive">
        {#each run.failures as f (f.target + f.reason)}
          <li>{f.target}：{f.reason}</li>
        {/each}
      </ul>
    {/if}
  </div>
{/snippet}

<Card.Root size="sm" role="article" aria-label={card.title} class="text-sm">
  <Card.Content class="grid gap-2">
    <div class="flex flex-wrap items-center gap-2">
      <h2 class="text-base font-medium" aria-label={card.title}><ConceptTerm id={familyConcept(card.id)}><a class="hover:underline" href="#data/{card.id}">{card.title}</a></ConceptTerm></h2>
      <span class="rounded-sm bg-muted px-1.5 py-0.5 text-xs text-foreground">{kindLabel(card.kind)}</span>
    </div>
    <p class="text-xs text-muted-foreground"><CodeText text={card.purpose} /></p>
    {#if card.sources?.length}<div class="flex flex-wrap gap-2 text-xs">{#each card.sources as source}<span><SourceBadge source={source.id} /> {source.note}</span>{/each}</div>{/if}
    {#if card.today_sources}
      {@const sources = card.today_sources}
      {@const targets = sources.primary + sources.backup + sources.failed}
      <p class="text-xs">今日来源：主源 {sources.primary} · 备源 {sources.backup} · 失败 {sources.failed}</p>
      <p class="text-xs text-muted-foreground">{targets ? `备源占目标 ${(sources.backup / targets * 100).toFixed(1)}%` : '今日尚无采集目标'}</p>
    {/if}
    <p>覆盖
      <span class="tabular-nums {card.coverage.lagging.length ? 'text-warn font-medium' : ''}">
        {card.coverage.covered} / {card.coverage.targets}</span>
      {#if card.coverage.lagging.length}
        <span class="text-xs text-warn">（滞后 {card.coverage.lagging.length}）</span>
      {/if}
      · 最新 {latestText(card.latest_at)}
      <span class="text-xs text-muted-foreground" title={card.coverage_note}>（{card.coverage_note}）</span>
    </p>
    {#if card.current_error}
      <p class="text-xs text-warn">当前范围文件错误：{card.current_error}</p>
    {/if}
    {#if card.last_run}
      {@render runLine('最近全族拉取', card.last_run, false)}
    {:else}
      <p class="text-xs text-muted-foreground">最近全族拉取 无记录</p>
    {/if}
    {#if card.last_ticker_run}
      {@render runLine('最近单只拉取', card.last_ticker_run, true)}
    {/if}
    {#if card.last_success_at}
      <p class="text-xs text-muted-foreground">最近成功 {fmtBeijing(card.last_success_at)}</p>
    {/if}
    {#if notes.length}
      <ul class="grid gap-0.5 text-xs text-warn">
        {#each notes as n (n)}<li>{noteText(n)}</li>{/each}
      </ul>
    {/if}
    {#if evidence}<p class="text-xs text-muted-foreground"><CodeText text={evidence} /></p>{/if}
    {#if card.job}
      {@const job = card.job}
      <p class="text-xs text-muted-foreground">所属任务
        <a class="underline" href="#tools/tasks">
          {job.name} · {job.enabled ? '开' : '关'} · 下次 {job.next_run_at ? fmtBeijing(job.next_run_at) : '—'}
        </a>
      </p>
    {:else}
      <p class="text-xs text-muted-foreground">所属任务 无</p>
    {/if}
    <div class="flex flex-wrap items-center gap-2">
      <Button size="sm" {busy} busyText="采集中…" onclick={fetchAll}>{fetchButtonLabel(card)}</Button>
      {#if error}<span class="text-xs text-destructive">{error}</span>{/if}
      {#if note}<span class="text-xs text-muted-foreground">{note}</span>{/if}
    </div>
  </Card.Content>
</Card.Root>
