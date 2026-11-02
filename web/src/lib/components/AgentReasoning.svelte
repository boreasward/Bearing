<script lang="ts">
  import { onMount } from 'svelte'
  import { Button } from '$lib/ui/button/index'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import AgentReasoning from './AgentReasoning.svelte'
  import ConceptTerm from './ConceptTerm.svelte'
  import KnowledgeRefs from './KnowledgeRefs.svelte'
  import ResearchFacts from './research/Facts.svelte'
  import { RESEARCH_PHASE_TEXT } from '$lib/research'
  import * as api from '../api'
  import { CONSISTENCY_TEXT } from '../derivation'
  import { AGENT_REASON_TEXT } from '../workbench'
  import { fmtBeijing } from '../format'
  import { isKnowRef, knowHref } from '../growth'
  import { TOOL_TEXT, type ReasoningCall, type ReasoningStep, type ReasoningView } from '../knowledge'
  import * as tl from '../timeline'

  // Agent 推理阅读视图（spec §8.6）：展开时才挂载，挂载时请求一次；步骤顺序、概念、校验结果都来自接口。
  let { runId }: { runId: string } = $props()

  let data = $state<ReasoningView | null>(null)
  let error = $state('')
  let root: HTMLElement | undefined
  let highlighted = $state<number | null>(null)
  let shownResult = $state<Record<number, boolean>>({})
  let fullList = $state<Record<string, boolean>>({})
  let nested = $state<Record<string, boolean>>({})

  onMount(() => {
    const id = runId
    api.getReasoning(id).then(next => { data = next }).catch((e: unknown) => {
      error = String((e as Error)?.message ?? e)
    })
  })

  const MODE_TEXT: Record<string, string> = {
    propose: '提议', revise: '修订', review: '复查', interpret: '解读', retro: '复盘起草', discuss: '讨论',
    research: '指定标的研究', pm: '组合经理', optimize: '优化提案', brief: '实施 brief',
  }
  const KNOWLEDGE_CALL_NOTE = '我的知识按截止时间读取：只含截止时间之前写入的版本'
  const RUN_STATUS_TEXT: Record<string, string> = {
    ok: '成功', pass: '不建议买入', rejected: '已作废', error: '出错', skipped: '跳过',
  }
  const LEVEL_TEXT: Record<string, string> = { entry_low: '入场下限', entry_high: '入场上限', stop: '止损', target1: 'T1' }
  const CHECK_TEXT: Record<string, string> = {
    pass: '通过', implicated: '可能未通过', not_recorded: '未单独记录', not_run: '未运行',
  }
  const CHECK_CLASS: Record<string, string> = {
    pass: 'text-muted-foreground', implicated: 'text-warn', not_recorded: 'text-muted-foreground', not_run: 'text-muted-foreground',
  }
  const ACTION_TEXT: Record<string, string> = { buy: '买入', pass: '不建议买入' }

  // 提议 / 复查 / 修订里没有存下来的步骤：接口把它们省掉或不给内容，页面只说明整次运行的结局。
  // 后端对没存下来的步骤给 available: false 与说明；万一整个内容步骤都没有，页面自己补一句。
  const CONTENT_KINDS = ['proposal', 'verdict', 'interpretation']
  const rejectedWithoutContent = $derived(
    !!data && !['ok', 'pass'].includes(data.run.status)
    && !data.steps.some(step => CONTENT_KINDS.includes(step.kind)))
  const toolName = (call: ReasoningCall) => call.tool_text ?? TOOL_TEXT[call.tool] ?? call.tool
  const text = (value: unknown): string =>
    typeof value === 'string' ? value : value == null ? '无' : JSON.stringify(value)
  const implicatedNote = (step: ReasoningStep): string | null => {
    if (step.note) return step.note
    const names = (step.items ?? []).filter(item => item.result === 'implicated').map(item => String(item.text))
    return names.length
      ? `未通过的检查在这几项之中：${names.join('、')}；校验器遇到第一处失败就停，不记录是哪一项` : null
  }

  function jump(ref: string) {
    const seq = Number(ref.split(':')[1])
    if (!Number.isFinite(seq)) return
    highlighted = seq
    // data-call carries this run's id so a nested AgentReasoning's calls are never matched
    const row = root?.querySelector(`[data-call="${runId}-${seq}"]`)
    row?.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
  }
  const consistencyOf = (value: ReasoningStep['consistency']): string | null =>
    value == null ? null : typeof value === 'string' ? value : value.status ?? null
  const thesisLabel = (value: unknown) => typeof value === 'number' ? `T${value}` : String(value)
  // fields keyed by something that is not a top-level key of the result: macro series, rule ids of get_trigger
  const extraFields = (call: ReasoningCall): [string, string][] => {
    const top = new Set(resultEntries(call.result).map(([key]) => key))
    return Object.entries(call.fields ?? {}).filter(([key]) => !top.has(key))
  }
  const resultEntries = (result: unknown): [string, unknown][] =>
    Array.isArray(result) ? [['结果', result]]
      : result && typeof result === 'object' ? Object.entries(result as Record<string, unknown>)
      : [['结果', result]]
  const listKey = (seq: number, key: string) => `${seq}|${key}`
</script>

{#snippet refList(list: string[] | undefined)}{#if list?.length}（{#each list as ref, i}{#if i}、{/if}{#if tl.isCallRef(ref)}<Button variant="link" size="sm" class="select-text text-[length:inherit] font-normal" onclick={() => jump(ref)}>{ref}</Button>{:else if isKnowRef(ref)}<a class="text-primary underline" href={knowHref(ref)}>{ref}</a>{:else}<span>{ref}</span>{/if}{/each}）{/if}{/snippet}

{#snippet conditionList(label: string, list: any[] | undefined)}
  {#if list?.length}
    <ul aria-label={label} class="list-disc ml-5 text-xs">
      {#each list as cond}<li>{cond.text ?? tl.conditionText(cond.kind, cond.params)}{@render refList(cond.refs)}</li>{/each}
    </ul>
  {/if}
{/snippet}

<section bind:this={root} aria-label="推理过程" class="text-sm border-l-2 border-border pl-2 space-y-3">
  {#if error}
    <DataBlock state="error" message={error} />
  {:else if !data}
    <DataBlock state="loading" skeleton="block" />
  {:else}
    {@const run = data.run}
    <p class="text-xs text-muted-foreground">
      {run.ticker} · {run.signal_date} · {MODE_TEXT[run.mode] ?? run.mode} · {run.agent_version}{run.model ? ` · ${run.model}` : ''}
      {#if run.cutoff_at} · 截止 {fmtBeijing(run.cutoff_at)}{/if}
    </p>
    {#if data.version}
      <p class="text-xs text-muted-foreground">这次运行产出第 {data.version.version_no} 版 · {tl.ORIGIN_TEXT[data.version.origin as tl.Origin] ?? data.version.origin}
        · <a class="text-primary underline" href={`#plan/${data.version.plan_id}`}>看计划页</a></p>
    {/if}
    {#each data.notes as note}<p class="text-xs text-warn">{note}</p>{/each}
    {#if data.knowledge_refs?.length}<KnowledgeRefs refs={data.knowledge_refs} />{/if}
    {#if !['ok', 'pass'].includes(run.status)}
      <p class="text-xs text-warn">本次运行状态：{RUN_STATUS_TEXT[run.status] ?? run.status}（{run.reason_text ?? run.reason ?? '未知原因'}）</p>
    {/if}
    {#if rejectedWithoutContent}
      <p class="text-xs">这次运行作废：{run.reason_text ?? run.reason ?? '未知原因'}，只保留工具调用</p>
    {/if}

    {#each data.steps as step, index (index)}
      <section aria-label={step.title} class="space-y-1">
        <h4 aria-label={step.title} class="font-medium text-xs">
          {#if step.concept}<ConceptTerm id={step.concept}>{step.title}</ConceptTerm>{:else}{step.title}{/if}
        </h4>
        {#if step.note && step.kind !== 'check'}<p class="text-xs text-muted-foreground">{step.note}</p>{/if}

        {#if step.kind === 'research'}
          <p class="text-xs text-muted-foreground">{RESEARCH_PHASE_TEXT[step.status]}{step.status === 'carried' && step.original_status ? `（原状态：${RESEARCH_PHASE_TEXT[step.original_status]}）` : ''}{step.reason ? `：${AGENT_REASON_TEXT[step.reason] ?? step.reason}` : ''}</p>
          {#if step.status === 'carried'}
            <!-- R16: a carried segment keeps the ORIGINAL run's call numbers; they are never resolved against this run's calls -->
            <p class="text-xs text-muted-foreground">{step.carried_from ? '引用编号属于原运行，不在本次运行的调用记录里' : '未记录原运行，引用编号无法对照'}</p>
            {#if step.carried_from}
              <Button variant="link" size="sm" class="text-[length:inherit] font-normal" aria-expanded={!!nested[step.carried_from]}
                      onclick={() => nested[step.carried_from!] = !nested[step.carried_from!]}>看原运行的推理</Button>
              {#if nested[step.carried_from]}<AgentReasoning runId={step.carried_from} />{/if}
            {/if}
          {/if}
          {#if step.output}<ResearchFacts output={step.output} onRef={step.status === 'carried' ? undefined : jump} />
          {:else}<DataBlock state="not_computed" message={step.reason_text ?? (step.reason ? AGENT_REASON_TEXT[step.reason] ?? step.reason : '本版未保存公司研究内容')} />{/if}
        {:else if step.kind === 'previous_chain'}
          <pre class="text-xs whitespace-pre-wrap rounded-sm border border-border p-2" data-previous-chain>{step.text}</pre>
        {:else if step.kind === 'read'}
          <ul class="space-y-1">
            {#each step.calls ?? [] as call (call.seq)}
              <li data-call={`${runId}-${call.seq}`} data-highlighted={highlighted === call.seq ? 'true' : undefined}
                  class="text-xs rounded-sm px-1 {highlighted === call.seq ? 'bg-warn/10 ring-1 ring-warn' : ''}">
                <p class="break-all">
                  <span class="font-medium">{call.ref}</span> ·
                  {#if call.concept}<ConceptTerm id={call.concept}>{toolName(call)}</ConceptTerm>{:else}{toolName(call)}{/if}
                  · <code>{text(call.args)}</code> · 哈希 {call.result_sha256_8}
                  <Button variant="link" size="sm" class="text-[length:inherit] font-normal" aria-expanded={!!shownResult[call.seq]}
                          onclick={() => shownResult[call.seq] = !shownResult[call.seq]}>{shownResult[call.seq] ? '收起返回值' : '看返回值'}</Button>
                </p>
                {#if call.cutoff_exempt}
                  <p class="text-warn">{call.cutoff_note ?? '概念卡随代码版本走，不受截止时间约束'}</p>
                {/if}
                {#if call.tool === 'get_knowledge'}<p class="text-warn">{KNOWLEDGE_CALL_NOTE}</p>{/if}
                {#if call.error}<p class="text-destructive">工具返回错误：{text(call.error)}</p>{/if}
                {#if shownResult[call.seq]}
                  <div class="ml-3 space-y-0.5">
                    {#if extraFields(call).length}
                      <p class="text-muted-foreground">涉及：{#each extraFields(call) as [name, concept], i (i)}{#if i}、{/if}<ConceptTerm id={concept}>{name}</ConceptTerm>{/each}</p>
                    {/if}
                    {#each resultEntries(call.result) as [key, value] (key)}
                      {#if Array.isArray(value)}
                        {@const all = !!fullList[listKey(call.seq, key)]}
                        {@const shown = all || value.length <= 4 ? value : [...value.slice(0, 2), ...value.slice(-2)]}
                        <p>
                          {#if call.fields[key]}<ConceptTerm id={call.fields[key]}>{key}</ConceptTerm>{:else}{key}{/if}（{value.length} 条）
                          {#if value.length > 4}
                            <Button variant="link" size="sm" class="text-[length:inherit] font-normal"
                                    onclick={() => fullList[listKey(call.seq, key)] = !all}>{all ? '只看首尾' : '展开全部'}</Button>
                          {/if}
                        </p>
                        <ul class="ml-3 break-all">
                          {#each shown as item, i}
                            <li>{#if call.tool === 'get_knowledge' && isKnowRef(String(item?.ref))}<a class="text-primary underline" href={knowHref(String(item.ref))}>{item.ref}</a> {item.title ?? ''}{:else}{text(item)}{/if}</li>
                            {#if !all && value.length > 4 && i === 1}<li class="text-muted-foreground">…</li>{/if}
                          {/each}
                        </ul>
                      {:else}
                        <p class="break-all">
                          {#if call.fields[key]}<ConceptTerm id={call.fields[key]}>{key}</ConceptTerm>{:else}{key}{/if}：{text(value)}
                        </p>
                      {/if}
                    {/each}
                  </div>
                {/if}
              </li>
            {/each}
          </ul>

        {:else if step.kind === 'proposal'}
          {#if step.available === false}
            <p class="text-xs">{step.text}</p>
          {/if}
          {#if step.action || step.summary}
            <p>{step.action ? (step.action_text ?? ACTION_TEXT[step.action] ?? step.action) : ''}{step.action && step.summary ? ' · ' : ''}{step.summary ?? ''}</p>
          {/if}
          {#if step.levels?.length}
            <ul aria-label="价位" class="text-xs">
              {#each step.levels as level, i (i)}
                <li>{level.label ?? LEVEL_TEXT[level.key] ?? level.key} {level.value}{@render refList(level.refs)}
                  {#if level.concept}<ConceptTerm id={level.concept} />{/if}</li>
              {/each}
            </ul>
          {/if}
          {#if step.thesis?.length || step.reasons?.length}
            {@const entries = step.thesis ?? step.reasons ?? []}
            <p class="text-xs text-muted-foreground">{data.legacy ? '理由' : '论点'}</p>
            <ol aria-label={data.legacy ? '理由' : '论点'} class="ml-1 space-y-0.5 text-xs">
              {#each entries as t, i}
                <li>{#if !data.legacy}<span class="px-1 rounded-sm bg-muted">{(t as any).id ?? `T${i + 1}`}</span> {/if}{t.text}{@render refList(t.refs)}</li>
              {/each}
            </ol>
          {/if}
          {@render conditionList('失效条件', step.invalidation)}
          {@render conditionList('下次复查条件', step.review_when)}
          {#if step.diff?.length}
            <p class="text-xs text-muted-foreground">相对上版 · 宿主差异</p>
            <ul class="list-disc ml-5 text-xs">
              {#each step.diff as d}
                <li>{tl.CHANGE_FIELD_TEXT[d.field] ?? d.field} {tl.diffValueText(d.field, d.before)} → {tl.diffValueText(d.field, d.after)}</li>
              {/each}
            </ul>
          {/if}
          {#if step.changes?.length}
            <p class="text-xs text-muted-foreground">模型说明</p>
            <ul class="list-disc ml-5 text-xs">
              {#each step.changes as c}<li>{tl.CHANGE_FIELD_TEXT[c.field] ?? c.field}：{c.text}{@render refList(c.refs)}</li>{/each}
            </ul>
          {/if}

        {:else if step.kind === 'check'}
          <ul class="text-xs space-y-0.5">
            {#each step.items ?? [] as item, i (i)}
              <li>{item.text} · <span class={CHECK_CLASS[item.result] ?? 'text-muted-foreground'}>{CHECK_TEXT[item.result] ?? item.result}</span></li>
            {/each}
          </ul>
          {#if implicatedNote(step)}<p class="text-xs text-warn">{implicatedNote(step)}</p>{/if}
          {#if step.reason_text && (step.items ?? []).some(item => item.result === 'not_run')}
            <p class="text-xs text-muted-foreground">{step.reason_text}</p>
          {/if}

        {:else if step.kind === 'counter'}
          {#if step.available === false}<p class="text-xs">{step.text}</p>{/if}
          {#if step.items?.length}
            <ul aria-label="反证" class="text-xs space-y-0.5">
              {#each step.items as c}
                <li><span class="px-1 rounded-sm bg-muted">{thesisLabel(c.thesis)}</span> {c.text}{@render refList(c.refs)}</li>
              {/each}
            </ul>
          {/if}
          {#if step.risks?.length}
            <p class="text-xs">其他风险：{#each step.risks as risk, i}{#if i}；{/if}{risk.text}{@render refList(risk.refs)}{/each}</p>
          {/if}
          {#if step.uncountered?.length}
            <p class="text-xs text-muted-foreground">反方未对这些论点给出反证：{step.uncountered.map(thesisLabel).join('、')}</p>
          {/if}
          {#if step.available !== false && step.dropped != null}<p class="text-xs text-muted-foreground">丢弃 {step.dropped} 条无效反证</p>{/if}

        {:else if step.kind === 'response'}
          {#each (step as any).items ?? [] as item (item.review_id)}
            <div class="text-xs space-y-0.5">
              <p>{item.session} · {tl.TRIGGER_TEXT[item.trigger] ?? item.trigger} · {tl.OUTCOME_TEXT[item.outcome as tl.Outcome] ?? item.outcome}
                {#if item.decided_by} · {tl.DECIDED_BY_TEXT[item.decided_by as tl.DecidedBy] ?? item.decided_by}{/if}
                {#if item.new_plan_id} → <a class="text-primary underline" href={`#plan/${item.new_plan_id}`}>第 {item.new_version_no} 版</a>{/if}</p>
              {#if item.reason}<p>{item.reason}</p>{/if}
              {#if item.signals_fired?.length}
                <p class="text-muted-foreground">信号：{item.signals_fired.map((k: string) => tl.SIGNAL_TEXT[k] ?? k).join('、')}</p>
              {/if}
              {#if item.changes?.length}
                <ul class="list-disc ml-5">
                  {#each item.changes as d}<li>{tl.CHANGE_FIELD_TEXT[d.field] ?? d.field} {tl.diffValueText(d.field, d.before)} → {tl.diffValueText(d.field, d.after)}</li>{/each}
                </ul>
              {/if}
            </div>
          {/each}

        {:else if step.kind === 'outcome'}
          <p class="text-xs">
            {#if step.plan_id}
              计划行：{step.status === 'invalid' ? `已作废（${(step.invalid_reasons ?? []).join('、')}）` : '有效'}
              {#if consistencyOf(step.consistency)} · {CONSISTENCY_TEXT[consistencyOf(step.consistency) as keyof typeof CONSISTENCY_TEXT] ?? consistencyOf(step.consistency)}{/if}
              · <a class="text-primary underline" href={`#plan/${step.plan_id}`}>看推导</a>
            {:else}
              这次运行没有生成计划行
            {/if}
          </p>

        {:else if step.kind === 'evidence'}
          <div class="text-xs space-y-0.5">
            {#if step.text}<p>{step.text}</p>{/if}
            {#if step.fact_keys?.length}<p>冻结事实：{step.fact_keys.map(k => `fact:${k}`).join('、')}</p>{/if}
            {#if step.session}<p>复查交易日 {step.session} · {tl.TRIGGER_TEXT[step.trigger ?? ''] ?? step.trigger ?? ''}</p>{/if}
            {#if step.signals?.length}
              <p>触发的信号：{step.signals.filter(s => s.fired).map(s => `${tl.SIGNAL_TEXT[s.kind] ?? s.kind}${s.detail ? `（${s.detail}）` : ''}`).join('、') || '无'}</p>
            {/if}
            {#if step.conditions?.length}
              <p>条件判定：{step.conditions.map(c => `${tl.conditionText(c.kind, c.params)}：${tl.CONDITION_RESULT_TEXT[c.result as tl.ConditionResultValue] ?? c.result}`).join('；')}</p>
            {/if}
            {#if step.user_input_ids?.length}<p>给模型的用户输入：{step.user_input_ids.map(i => `user:${i}`).join('、')}</p>{/if}
            {#if step.review_agent_run_id && step.review_agent_run_id !== runId}
              <Button variant="link" size="sm" class="text-[length:inherit] font-normal" aria-expanded={!!nested[step.review_agent_run_id]}
                      onclick={() => nested[step.review_agent_run_id!] = !nested[step.review_agent_run_id!]}>看复查推理</Button>
              {#if nested[step.review_agent_run_id]}<AgentReasoning runId={step.review_agent_run_id} />{/if}
            {/if}
            {#if step.revision_agent_run_id && step.revision_agent_run_id !== runId}
              <Button variant="link" size="sm" class="text-[length:inherit] font-normal" aria-expanded={!!nested[step.revision_agent_run_id]}
                      onclick={() => nested[step.revision_agent_run_id!] = !nested[step.revision_agent_run_id!]}>看修订推理</Button>
              {#if nested[step.revision_agent_run_id]}<AgentReasoning runId={step.revision_agent_run_id} />{/if}
            {/if}
          </div>

        {:else if step.kind === 'verdict'}
          {#if step.available === false}<p class="text-xs">{step.text}</p>{/if}
          <p class="text-xs">{step.outcome_text ?? tl.OUTCOME_TEXT[step.outcome as tl.Outcome] ?? step.outcome ?? ''}
            {#if step.decided_by} · {tl.DECIDED_BY_TEXT[step.decided_by as tl.DecidedBy] ?? step.decided_by}{/if}</p>
          {#if step.reason}<p class="text-xs">{step.reason}{@render refList(step.refs)}</p>{/if}

        {:else if step.kind === 'interpretation'}
          {#if step.available === false}<p class="text-xs">{step.text}</p>{/if}
          {#if step.summary}<p>{step.summary}</p>{/if}
          {#each [['支持', step.support], ['反证', step.contra], ['冲突与缺口', step.conflicts]] as [label, list]}
            {#if Array.isArray(list) && list.length}
              <p class="text-xs text-muted-foreground">{label}</p>
              <ul class="list-disc ml-5 text-xs">{#each list as item}<li>{item.text}{@render refList(item.refs)}</li>{/each}</ul>
            {/if}
          {/each}

        {:else if step.kind === 'draft'}
          {#if step.available === false}<p class="text-xs">{step.text}</p>{/if}
          {#if step.summary}<p class="font-medium whitespace-pre-wrap">{step.summary}</p>{/if}
          {#if step.items?.length}
            <ol class="list-decimal ml-5 text-xs space-y-0.5">
              {#each step.items as item}<li><span class="whitespace-pre-wrap">{item.text}</span>{@render refList(item.refs)}</li>{/each}
            </ol>
          {/if}
          {#if step.lessons?.length}
            <p class="text-xs text-muted-foreground">教训草稿</p>
            <ul class="space-y-1 text-xs">
              {#each step.lessons as lesson}
                <li class="border-l border-border pl-2">
                  <p class="font-medium">{lesson.title}</p>
                  <p class="whitespace-pre-wrap">{lesson.body}</p>
                  <p>适用范围：{lesson.scope}</p>
                  <p>反例：{lesson.counter_examples}</p>
                  {#if lesson.refs?.length}<p class="text-muted-foreground">依据{@render refList(lesson.refs)}</p>{/if}
                </li>
              {/each}
            </ul>
          {/if}

        {:else if step.kind === 'question'}
          {#if step.text}<p class="whitespace-pre-wrap">{step.text}</p>{/if}
          {#if step.attachment}<p class="text-xs text-muted-foreground whitespace-pre-wrap">{step.attachment}</p>{/if}
          {#if step.context}<p class="text-xs text-muted-foreground whitespace-pre-wrap">{step.context}</p>{/if}

        {:else if step.kind === 'answer'}
          {#if step.available === false}<p class="text-xs">{step.text}</p>
          {:else if step.text}<p class="whitespace-pre-wrap">{step.text}</p>{/if}
          {#if step.refs?.length}<p class="text-xs text-muted-foreground">引用{@render refList(step.refs)}</p>{/if}
          {#if step.knowledge_draft}
            {@const draft = step.knowledge_draft}
            <div class="text-xs border-l border-border pl-2">
              <p class="text-muted-foreground">经验草稿</p>
              <p class="font-medium">{draft.title}</p>
              <p class="whitespace-pre-wrap">{draft.body}</p>
              <p>适用范围：{draft.scope}</p>
              <p>反例：{draft.counter_examples}</p>
            </div>
          {/if}
        {/if}
      </section>
    {/each}
  {/if}
</section>
