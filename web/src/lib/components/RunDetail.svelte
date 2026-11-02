<script lang="ts">
  import { onDestroy } from 'svelte'
  import { Alert, AlertDescription } from '$lib/ui/alert/index'
  import { Button } from '$lib/ui/button/index'
  import * as Card from '$lib/ui/card/index'
  import ConfirmDialog from '$lib/ui/confirm-dialog/ConfirmDialog.svelte'
  import CutoffTime from '$lib/ui/domain/CutoffTime.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import Icon from '$lib/ui/domain/Icon.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import AgentReasoning from './AgentReasoning.svelte'
  import FetchTable from './FetchTable.svelte'
  import NodeGraph from './NodeGraph.svelte'
  import PortfolioResult from './account/PortfolioResult.svelte'
  import * as api from '../api'
  import { fmtBeijing } from '../format'
  import {
    NODE_STATUS_TEXT, RESUME_POLL_MAX_MS, RESUME_POLL_MS, RESUME_STILL_RUNNING_TEXT, RETRO_PUSH_TEXT, RETRO_STATUS_TEXT,
    RUN_STATUS_TEXT, TRIGGER_TEXT,
    durationText, nyDay, resumeErrorText,
  } from '../runs'
  import type { DataRequestCounts, RunDetail, RunNode } from '../runs'
  import { OUTCOME_TEXT } from '../timeline'
  import { REGIME_LABELS, describeError, newRequestId, ruleLabel } from '../workbench'

  // 运行详情页（spec §7.3）：一句话、元信息、执行记录、节点状态图、选中节点的三块、原始摘要。
  // 轮询只读 GET /api/runs/{run_id}/detail；全部文本按纯文本显示。
  let { runId }: { runId: string } = $props()

  const AGENT_STATUS_TEXT: Record<string, string> = {
    ok: '成功', pass: '不建议买入', rejected: '已作废', error: '出错', skipped: '跳过',
  }
  const MODE_TEXT: Record<string, string> = { propose: '建议', interpret: '解读', review: '复查' }
  const RETRO_KIND_TEXT: Record<string, string> = { card: '复盘卡', weekly: '周度复盘', monthly: '月度复盘' }
  const INTERNAL_RESULT_TEXT: Record<string, string> = { inserted: '新写入', existing: '已存在' }
  const REQUEST_COUNT_TEXT: Record<keyof DataRequestCounts, string> = { queued: '排队中', fetched: '已补采', failed: '失败', deferred: '暂缓', reruns: '重跑' }
  const requestCounts = (value: DataRequestCounts) => (Object.keys(REQUEST_COUNT_TEXT) as (keyof DataRequestCounts)[]).map(key => `${REQUEST_COUNT_TEXT[key]} ${value[key]}`).join(' · ')
  const PROPOSAL_SOURCE_TEXT: Record<string, string> = { data: '数据', agent_quality: 'Agent 质量', route: '路线' }
  const COMMENTARY_TEXT: Record<string, string> = { ok: '组合经理已完成', error: '组合经理出错', rejected: '组合经理未通过校验', missing: '仅有硬结果', skipped: '组合经理跳过', disabled: '组合经理已关闭' }

  let detail = $state<RunDetail | null>(null)
  let error = $state('')
  let selected = $state<string | null>(null)
  let touched = false                 // the user picked a node: later reloads keep the choice

  // resume: confirm box -> POST -> poll
  let confirming = $state(false)
  let resumeBusy = $state(false)
  let resumeError = $state('')
  let resumeNotice = $state('')
  let resumeRequestId: string | null = null   // kept across a failed attempt, so a retry is the same request

  // rerun agent, per unit row
  let rerunBusy = $state<Record<string, boolean>>({})
  let rerunText = $state<Record<string, string>>({})
  let rerunDone = $state<Record<string, boolean>>({})
  const rerunIds: Record<string, string> = {}
  let reasoningOpen = $state<Record<string, boolean>>({})
  let rerunTarget = $state<any>(null)         // the unit the confirm dialog is about
  let rerunOpen = $state(false)
  // how long the page has been waiting for a running run (shown beside the 运行中 pill)
  let pollSince = $state<number | null>(null)
  let pollNow = $state(Date.now())

  let destroyed = false
  let timer: ReturnType<typeof setTimeout> | null = null
  let generation = 0

  function stopPolling() { if (timer) clearTimeout(timer); timer = null; pollSince = null }

  function describe(e: unknown): string {
    if (e instanceof api.ApiError) {
      if (e.status === 400) return '运行 ID 格式不合法'
      if (e.status === 404) return '没有这次运行'
    }
    return describeError(e)
  }

  function pickDefault(d: RunDetail): string | null {
    const nodes = d.nodes ?? []
    return (nodes.find(n => n.status === 'failed') ?? nodes.find(n => n.status === 'degraded' || n.status === 'partial'))?.id ?? null
  }
  function apply(next: RunDetail) {
    detail = { ...next, nodes: next.nodes ?? [], executions: next.executions ?? [] }
    if (!touched || !detail.nodes.some(n => n.id === selected)) selected = pickDefault(detail)
  }

  async function load(token: number) {
    try {
      const next = await api.getRunDetail(runId)
      if (destroyed || token !== generation) return
      apply(next)
      error = ''
      if (next.status === 'running') startPolling(next, true)
    } catch (e) {
      if (destroyed || token !== generation) return
      error = describe(e)
    }
  }

  $effect(() => {
    void runId
    const token = ++generation
    stopPolling()
    detail = null; error = ''; selected = null; touched = false
    confirming = false; resumeError = ''; resumeNotice = ''; resumeRequestId = null
    reasoningOpen = {}; rerunText = {}; rerunDone = {}
    void load(token)
    return () => { generation++; stopPolling() }
  })
  onDestroy(() => { destroyed = true; stopPolling() })

  // ---- polling (setTimeout chain; stops when the run is no longer running, after 60 minutes, or on destroy) ----
  function startPolling(from: RunDetail, sawRunning: boolean) {
    stopPolling()
    const token = generation
    const started = Date.now()
    pollSince = started
    pollNow = started
    const baseline = from.executions?.length ?? 0
    let seen = sawRunning
    let misses = 0
    const tick = async () => {
      timer = null
      if (destroyed || token !== generation) return
      try {
        const next = await api.getRunDetail(runId)
        if (destroyed || token !== generation) return
        pollNow = Date.now()
        apply(next)
        error = ''
        if (next.status === 'running') seen = true
        // The first reads after a resume can still show the old state: stop only after we saw it run,
        // saw a new execution, or three reads in a row said it is not running.
        else if (seen || (next.executions?.length ?? 0) > baseline || ++misses >= 3) {
          resumeNotice = seen || (next.executions?.length ?? 0) > baseline ? '' : '未看到续跑开始，请稍后刷新'
          pollSince = null
          return
        }
      } catch { /* a missed read is retried on the next tick */ }
      if (Date.now() - started >= RESUME_POLL_MAX_MS) { resumeNotice = RESUME_STILL_RUNNING_TEXT; pollSince = null; return }
      timer = setTimeout(tick, RESUME_POLL_MS)
    }
    timer = setTimeout(tick, RESUME_POLL_MS)
  }

  // ---- resume ----
  const resumeIndex = $derived(detail?.resume?.node ? detail.nodes.findIndex(n => n.id === detail!.resume!.node) : -1)
  const resumeNode = $derived<RunNode | null>(resumeIndex >= 0 && detail ? detail.nodes[resumeIndex] : null)
  const resumeText = $derived(resumeNode && detail
    ? `从「${resumeNode.title}」继续这次运行？已完成的 ${resumeIndex} 个节点不会重跑；截止时间仍是 ${fmtBeijing(detail.cutoff_at)}。` : '')

  const nodeTitle = (id: string | null | undefined) => detail?.nodes.find(n => n.id === id)?.title ?? id ?? null

  function resumeFailText(e: unknown): string {
    if (e instanceof api.ApiError) {
      if (e.status === 409) {
        const known = e.code ? resumeErrorText(e.code, {
          next: nodeTitle(detail?.resume?.node), day: nyDay(detail?.resume?.deadline_at),
        }) : null
        return known ?? `不能继续：${e.message}`
      }
      if (e.status === 404) return '没有这次运行'
    }
    return describeError(e)
  }

  async function doResume() {
    if (!detail || !resumeNode || resumeBusy) return
    resumeBusy = true; resumeError = ''
    resumeRequestId ??= newRequestId()
    try {
      const accepted = await api.resumeDaily(detail.run_id, resumeNode.id, resumeRequestId)
      if (destroyed) return
      resumeRequestId = null
      resumeNotice = `已提交，第 ${accepted.attempt} 次执行，页面每 5 秒刷新一次`
      startPolling(detail, false)
    } catch (e) {
      if (destroyed) return
      resumeError = resumeFailText(e)
      if (e instanceof api.ApiError && e.status === 409) { resumeRequestId = null; void load(generation) }
    } finally { resumeBusy = false; confirming = false }       // the dialog hides the page: show the outcome there
  }

  // ---- rerun agent ----
  /** not_needed -> the spec text; any other 409 shows what the backend said (it may already be Chinese). */
  function rerunFailText(e: unknown): string {
    if (e instanceof api.ApiError && e.status === 409)
      return e.code === 'not_needed' ? '这个单元不需要重跑' : e.message
    return describeError(e)
  }
  const unitKey = (item: any) => `${item.ticker}|${item.signal_date}|${item.mode}`
  async function rerun(item: any) {
    const key = unitKey(item)
    if (rerunBusy[key]) return
    rerunBusy[key] = true
    rerunText[key] = ''
    rerunIds[key] ??= newRequestId()
    try {
      const res = await api.rerunAgent({
        ticker: item.ticker, signal_date: item.signal_date, mode: item.mode, request_id: rerunIds[key], origin: 'run_detail',
      })
      if (destroyed) return
      delete rerunIds[key]
      rerunText[key] = res.run_id ? '已重置尝试次数，并已重新运行' : '已重置尝试次数，同一交易日的续跑或手动运行时执行'
      rerunDone[key] = true
      void load(generation)
    } catch (e) {
      if (destroyed) return
      rerunText[key] = rerunFailText(e)
    } finally { rerunBusy[key] = false; rerunOpen = false }
  }
  const rerunConsequence = (item: any) =>
    `重置 ${item.ticker}（${item.signal_date}，${MODE_TEXT[item.mode] ?? item.mode}）的尝试次数，并重新运行这个单元的 Agent。`

  // ---- display helpers ----
  const selectedNode = $derived(detail?.nodes.find(n => n.id === selected) ?? null)
  const statusText = (s: string) => NODE_STATUS_TEXT[s] ?? RUN_STATUS_TEXT[s] ?? s
  const count = (v: number | null | undefined) => v == null ? '未记录' : String(v)
  const waitedSeconds = $derived(pollSince === null ? null : Math.max(0, Math.floor((pollNow - pollSince) / 1000)))
  const canAct = $derived(detail?.kind === 'daily')
  function select(id: string) { selected = id; touched = true }
  function retryNote(node: RunNode): string | null {
    const retry = node.did?.retry
    if (!retry) return null
    return `第一次失败：${retry.first_error}，${retry.waited_s} 秒后重试${node.status === 'failed' ? '仍失败' : '成功'}`
  }
</script>

<section aria-label="运行详情" class="grid gap-4 text-sm">
  <header class="flex flex-wrap items-center gap-3">
    <Button variant="link" size="sm" href="#tools/runs">返回运行记录</Button>
    <h1 class="font-mono text-xs text-muted-foreground">{runId}</h1>
  </header>

  {#if error}
    <DataBlock state="error" message={error} />
  {:else if !detail}
    <DataBlock state="loading" skeleton="block" />
  {:else}
    <div class="grid gap-1">
      {#if detail.headline}<p class="text-base font-medium" data-testid="headline">{detail.headline}</p>{/if}
      <p class="flex flex-wrap items-center gap-x-4 gap-y-0.5 text-xs text-muted-foreground" data-testid="meta">
        <span>任务 {detail.task}</span>
        <span class="inline-flex items-center gap-1.5">状态 <StatusPill domain="run" value={detail.status} />
          {#if waitedSeconds !== null && detail.status === 'running'}<span>已等待 {waitedSeconds} 秒</span>{/if}</span>
        {#if detail.trigger}<span>触发 {TRIGGER_TEXT[detail.trigger] ?? detail.trigger}</span>{/if}
        {#if detail.session}<span>session {detail.session}</span>{/if}
        {#if detail.cutoff_at}<CutoffTime at={detail.cutoff_at} />{/if}
        {#if detail.graph_version || detail.stages_version}
          <span>{[detail.graph_version, detail.stages_version].filter(Boolean).join(' / ')}</span>
        {/if}
      </p>
    </div>

    {#if detail.executions.length > 1}
      <Section title="执行记录" level={2}>
        <ol class="grid gap-0.5 text-xs">
          {#each detail.executions as ex (ex.report_id)}
            <li class="flex flex-wrap items-center gap-x-1.5">
              第 {ex.attempt} 次 · {fmtBeijing(ex.started_at)} · {TRIGGER_TEXT[ex.trigger_source] ?? ex.trigger_source}
              · <StatusPill domain="run" value={ex.status} />{ex.resume_node ? ` · 从「${detail.nodes.find(n => n.id === ex.resume_node)?.title ?? ex.resume_node}」续跑` : ''}
            </li>
          {/each}
        </ol>
      </Section>
    {/if}

    {#if detail.kind === 'other' || detail.nodes.length === 0}
      <DataBlock state="empty" message="这类运行没有节点证据" />
    {:else}
      <NodeGraph nodes={detail.nodes} {selected} onselect={select} />
      {#if resumeError}
        <Alert variant="danger"><Icon name="circle-x" /><AlertDescription>{resumeError}</AlertDescription></Alert>
      {:else if canAct && detail.status === 'failed' && detail.resume && !detail.resume.allowed && detail.resume.reason}
        <Alert variant="warn">
          <Icon name="triangle-alert" />
          <AlertDescription>{resumeErrorText(detail.resume.reason, { next: nodeTitle(detail.resume.node), day: nyDay(detail.resume.deadline_at) }) ?? detail.resume.reason}</AlertDescription>
        </Alert>
      {/if}
      {#if resumeNotice}<Alert variant="info"><Icon name="info" /><AlertDescription>{resumeNotice}</AlertDescription></Alert>{/if}

      {#if selectedNode}
        {@const node = selectedNode}
        <Card.Root size="sm" role="region" aria-label={`${node.title} 详情`}>
          <Card.Content class="grid gap-4">
          <h2 class="text-base font-medium">{node.title}</h2>

          <Section title="做了什么" level={3}>
            {#if node.did?.sentence}<p>{node.did.sentence}</p>{/if}
            <p class="text-xs text-muted-foreground">
              状态 {statusText(node.status)}{node.reason_text || node.reason ? `：${node.reason_text ?? node.reason}` : ''}
              · 输入 {count(node.did?.input_count)} · 产出 {count(node.did?.output_count)}
              {#if node.duration_ms != null} · 耗时 {durationText(node.duration_ms)}{/if}
            </p>
            {#if node.status === 'failed' && node.reason && node.reason !== node.reason_text}
              <p class="break-all text-xs text-destructive">{node.reason}</p>
            {/if}
            {#if node.attempts.length > 1}
              <ul class="text-xs text-muted-foreground">
                {#each node.attempts as a (a.attempt)}
                  <li>第 {a.attempt} 次：{statusText(a.status)}{a.duration_ms != null ? ` · ${durationText(a.duration_ms)}` : ''}</li>
                {/each}
              </ul>
            {/if}
            {#if retryNote(node)}<p class="text-xs text-warn">{retryNote(node)}</p>{/if}
            {#each node.did?.skips ?? [] as skip, i (i)}
              <p class="text-xs">{skip.text} × {skip.count}{skip.samples?.length ? `（样本：${skip.samples.join('、')}）` : ''}</p>
            {/each}
            {#each node.did?.failures ?? [] as failure, i (i)}
              <p class="break-all text-xs text-destructive">{failure.target}：{failure.reason}</p>
            {/each}
            {#each node.did?.degradations ?? [] as d, i (i)}
              <p class="text-xs text-warn">{d.text}{d.detail ? `：${d.detail}` : ''}</p>
            {/each}

            {#if canAct && node.actions?.resume && detail.resume?.allowed && resumeNode?.id === node.id}
              <div>
                <Button variant="primary" size="sm" onclick={() => { confirming = true; resumeError = '' }}>从此节点继续</Button>
              </div>
              <ConfirmDialog bind:open={confirming} title="从此节点继续" consequence={resumeText} actionLabel="从此节点继续"
                             busy={resumeBusy} onConfirm={doResume} />
            {/if}
          </Section>

          {#if node.fetch?.families?.length}
            <Section title="拉取详情" level={3}>
              <FetchTable families={node.fetch.families} />
            </Section>
          {/if}

          {#if node.results}
            {@const results = node.results}
            <Section title="结果详情" level={3}>
              {#if results.kind === 'collect' && !results.items?.length}
                <p class="text-xs text-muted-foreground">拉取详情已列全</p>
              {:else if !results.items?.length}
                <p class="text-xs text-muted-foreground">没有结果</p>
              {:else}
                <p class="text-xs text-muted-foreground">
                  {results.truncated ? `只显示前 ${results.items.length} 项，共 ${results.total} 项` : `共 ${results.total} 项`}
                </p>
                <ul class="space-y-1">
                  {#each results.items as item, i (i)}
                    <li class="space-y-0.5 text-xs">
                      {#if results.kind === 'sec_monitor'}
                        <span><a class="text-primary underline" href={`#instrument/${encodeURIComponent(item.ticker)}`}>{item.ticker}</a>
                          · {item.form} · {item.filing_date} · {item.accession} · {item.notified ? '已通知' : '未通知'}</span>
                      {:else if results.kind === 'screen'}
                        <span><a class="text-primary underline" href={`#instrument/${encodeURIComponent(item.ticker)}`}>{item.ticker}</a>
                          · 排名 {count(item.rank)} · {item.group ?? '无分组'}{item.selected ? ' · 入选' : ''}{item.new_member ? ' · 新成员' : ''}</span>
                      {:else if results.kind === 'regime'}
                        <span>{REGIME_LABELS[item.label] ?? item.label} · {item.regime_id}</span>
                      {:else if results.kind === 'signals'}
                        <span><a class="text-primary underline" href={`#instrument/${encodeURIComponent(item.ticker)}`}>{item.ticker}</a>
                          · {ruleLabel(item.rule_id)} · {INTERNAL_RESULT_TEXT[item.status] ?? item.status}</span>
                      {:else if results.kind === 'plan'}
                        <span><a class="text-primary underline" href={`#plan/${item.plan_id}`}>{item.ticker}</a>
                          · {item.status === 'invalid' ? '已作废' : item.status === 'ok' ? '有效' : item.status}{item.invalid_reasons?.length ? `（${item.invalid_reasons.join('、')}）` : ''}</span>
                      {:else if results.kind === 'agent'}
                        {@const key = unitKey(item)}
                        <span class="flex flex-wrap items-baseline gap-x-2">
                          <a class="text-primary underline" href={`#instrument/${encodeURIComponent(item.ticker)}`}>{item.ticker}</a>
                          <span>{item.signal_date}</span>
                          <span>{MODE_TEXT[item.mode] ?? item.mode}</span>
                          <span>{AGENT_STATUS_TEXT[item.status] ?? item.status}{item.reason_text || item.reason ? `（${item.reason_text ?? item.reason}）` : ''}</span>
                          <span class="text-muted-foreground">第 {count(item.attempt)} 次 · 输入 {count(item.input_tokens)} · 产出 {count(item.output_tokens)} token{item.model ? ` · ${item.model}` : ''}</span>
                          {#if item.plan_id}<a class="text-primary underline" href={`#plan/${item.plan_id}`}>计划</a>{/if}
                          {#if item.run_id}
                            <Button variant="link" size="sm" class="text-[length:inherit]" aria-expanded={!!reasoningOpen[item.run_id]}
                                    onclick={() => reasoningOpen[item.run_id] = !reasoningOpen[item.run_id]}>看推理过程</Button>
                          {/if}
                          {#if canAct && item.can_rerun && !rerunDone[key]}
                            <Button size="sm" busy={rerunBusy[key]} onclick={() => { rerunTarget = item; rerunOpen = true }}>重新运行 Agent</Button>
                          {/if}
                          {#if rerunText[key]}<span class="text-muted-foreground">{rerunText[key]}</span>{/if}
                        </span>
                        {#if item.run_id && reasoningOpen[item.run_id]}<AgentReasoning runId={item.run_id} />{/if}
                      {:else if results.kind === 'review'}
                        <span><a class="text-primary underline" href={`#instrument/${encodeURIComponent(item.ticker)}`}>{item.ticker}</a>
                          · {item.signal_date} · {OUTCOME_TEXT[item.outcome as keyof typeof OUTCOME_TEXT] ?? item.outcome}
                          {#if item.plan_id} · <a class="text-primary underline" href={`#plan/${item.plan_id}`}>计划</a>{/if}</span>
                      {:else if results.kind === 'portfolio'}
                        <div class="grid gap-2 rounded-md border border-border p-2">
                          <a class="text-primary underline" href={`#account/${item.account_id}`}>{item.name}</a>
                          <div class="flex flex-wrap gap-2"><StatusPill domain="portfolio_verdict" value="ok" text={`可执行 ${count(item.ok)}`} /><StatusPill domain="portfolio_verdict" value="blocked" text={`被拦截 ${count(item.blocked)}`} /><StatusPill domain="portfolio_verdict" value="deferred" text={`暂缓 ${count(item.deferred)}`} /></div>
                          <p>{item.commentary_status == null ? '组合经理未运行（只有硬结果或检查未完成）' : COMMENTARY_TEXT[item.commentary_status] ?? item.commentary_status}</p>
                          <PortfolioResult accountId={item.account_id} name={item.name} session={detail.session} />
                        </div>
                      {:else if results.kind === 'proposals'}
                        <span class="flex flex-wrap items-center gap-2"><a class="text-primary underline" href={`#roadmap/proposals/${item.id}`}>{item.title}</a><span>{PROPOSAL_SOURCE_TEXT[item.source] ?? item.source}</span><StatusPill domain="proposal" value={item.status} /></span>
                      {:else if results.kind === 'push'}
                        <span>{item.sent ? `已发送 ${count(item.messages)} 条` : '未发送'}{item.channel ? ` · ${item.channel}` : ''}{item.reason ? ` · ${item.reason}` : ''}</span>
                      {:else if results.kind === 'retro'}
                        <span>
                          {#if item.kind === 'card'}
                            <a class="text-primary underline" href={`#review/cards/${item.id}`}>{RETRO_KIND_TEXT.card}{item.ticker ? ` ${item.ticker}` : ''}</a>
                          {:else}
                            <a class="text-primary underline" href={`#review/${item.kind}`}>{RETRO_KIND_TEXT[item.kind] ?? item.kind}</a>
                          {/if}
                          {item.status ? ` · ${RETRO_STATUS_TEXT[item.status] ?? item.status}` : ''}{item.push ? ` · ${typeof item.push === 'string' ? (RETRO_PUSH_TEXT[item.push] ?? item.push) : JSON.stringify(item.push)}` : ''}
                        </span>
                      {:else}
                        <span>{JSON.stringify(item)}</span>
                      {/if}
                    </li>
                  {/each}
                </ul>
              {/if}
            </Section>
          {/if}
          {#if node.id === 'agent'}
            {#if node.did?.research_requests != null}<Section title="研究请求" level={3}><p>{node.did.research_requests}</p><a class="text-primary underline" href="#research">查看研究档案</a></Section>{/if}
            {#if node.did?.data_requests != null}<Section title="补查请求" level={3}><p>{requestCounts(node.did.data_requests)}</p></Section>{/if}
          {/if}
          </Card.Content>
        </Card.Root>
      {:else}
        <p class="text-xs text-muted-foreground">点上面的格子，查看这个节点做了什么</p>
      {/if}
    {/if}

    <details class="text-xs">
      <summary class="cursor-pointer text-primary">原始摘要</summary>
      <pre class="mt-1 max-h-96 overflow-auto rounded-md bg-muted p-2">{JSON.stringify(detail.raw_summary, null, 2)}</pre>
    </details>
  {/if}

  <ConfirmDialog bind:open={rerunOpen} title="重新运行 Agent" consequence={rerunTarget ? rerunConsequence(rerunTarget) : ''}
                 actionLabel="重新运行 Agent" busy={rerunTarget ? !!rerunBusy[unitKey(rerunTarget)] : false}
                 onConfirm={() => rerun(rerunTarget)} />
</section>
