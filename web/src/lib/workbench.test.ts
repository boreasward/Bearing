import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  AGENT_REASON_TEXT, CONFLICT_TEXT, DECISIONS_RETIRED_TEXT, DECISION_LINK_RETIRED_TEXT, ENTRY_TYPE_TEXT,
  EXECUTION_TEXT, INSTRUCTION_TEXT, MIGRATION_029_TEXT, OPEN_CASE_TEXT, OPEN_SCENARIO_TEXT, STALE_HOLDING_TEXT, WorkbenchError,
  activeChainText, describeError, executionText, rowExecutionText, getAccount, getDiscipline, getHoldingStatus, getLinkablePlans, getMonthly,
  getPlanHistory, getPlanNotes, getPlanShadow, getPlanTable, getToday, holdingLine, levelsError, openCaseText,
  pct, postPlanNote, postTrade, publishedText, pushToday, RULE_LABELS, ruleLabel, signedPct, timeline,
  tradePlanText,
} from './workbench'
import type { DisciplineTrade, Plan, PlanShadow } from './workbench'

afterEach(() => vi.unstubAllGlobals())

const unit = {
  ticker: 'NVDA', signal_date: '2026-10-22', proposed_action: 'buy' as const, labels: [], candidate_ids: ['c1', 'c2'],
  rule_ids: ['trend_reclaim'], entry_passed: false, holding: false,
  candidates: [], brief: { id: 'b1', status: 'ok' as const }, filings: [], news: [], position: null,
}

const basePlan = {
  id: 'p1', ticker: 'NVDA', signal_date: '2026-10-22', plan_version: 'plan-1', status: 'ok' as const,
  entry_low: 98.5, entry_high: 101.5, stop: 94, target1: 112, ref_price: 100, atr: 2,
  valid_from: '2026-10-23', valid_until: '2026-10-27', sizing: null, invalid_reasons: [], warnings: [],
}

describe('own levels', () => {
  it('rejects own levels that break the ordering', () => {
    expect(levelsError({ entry_low: '99', entry_high: '101.5', stop: '100', target1: '112' })).toContain('止损')
    expect(levelsError({ entry_low: 'abc', entry_high: '101.5', stop: '94', target1: '112' })).toBeTruthy()
    expect(levelsError({ entry_low: '98.5', entry_high: '101.5', stop: '94', target1: '112' })).toBeNull()
  })
})

describe('requests', () => {
  it('keeps the HTTP status so the page can tell 503 from an empty list', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: '工作台数据表未就绪' }), { status: 503 })))
    const error = await getToday().catch(e => e)
    expect(error).toBeInstanceOf(WorkbenchError)
    expect(error.status).toBe(503)
    expect(describeError(error)).toBe('数据库未就绪：工作台数据表未就绪')
  })

  it('explains a 409 as a newer record', () => {
    expect(describeError(new WorkbenchError('this unit already has a newer decision', 409)))
      .toContain('已有更新的记录')
  })
})

describe('formatting', () => {
  it('never invents a publish time', () => {
    expect(publishedText(null)).toBe('发布时间未知')
    expect(publishedText('2026-10-22T12:00:00Z')).toContain('2026-10-22')
  })

  it('labels rules and formats percentages', () => {
    expect(ruleLabel('earnings_gap_up')).toBe('财报跳空向上')
    expect(ruleLabel('unknown_rule')).toBe('unknown_rule')
    expect(pct(0.0512)).toBe('+5.12%')
    expect(pct(-0.03)).toBe('-3.00%')
    expect(pct(null)).toBe('—')
  })
})

describe('timeline', () => {
  it('merges sources newest first and keeps undated news at its fetch time', () => {
    const items = timeline({
      ticker: 'NVDA', session: '2026-10-22', bars: [], position: null, closed: [],
      filings: [{ id: 1, form: '8-K', filing_date: '2026-10-20', accepted_at: '2026-10-20T20:00:00Z', items: ['2.02'] }],
      news: [{ id: 2, source: 'yfinance', title: 'Undated', url: 'https://x', publisher: null, published_at: null,
               fetched_at: '2026-10-21T10:00:00Z' }],
      candidates: [{ id: 'c', signal_date: '2026-10-22', action: 'buy', rule_id: 'trend_reclaim', created_at: '2026-10-22T22:30:00Z' }],
      decisions: [], trades: [],
    } as any)
    expect(items.map(i => i.kind)).toEqual(['candidate', 'news', 'filing'])
    expect(items[1].text).toContain('发布时间未知')
  })
})


describe('P1 endpoints', () => {
  it('hits account, plan shadow, discipline, monthly and push', async () => {
    const urls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      urls.push(`${init?.method ?? 'GET'} ${String(url)}`)
      return new Response('{}', { status: 200 })
    }))
    await getAccount(); await getPlanShadow(); await getDiscipline(); await getMonthly(); await pushToday()
    expect(urls).toEqual([
      'GET /api/workbench/account/snapshots', 'GET /api/workbench/plans/shadow',
      'GET /api/workbench/discipline', 'GET /api/workbench/monthly-reviews', 'POST /api/workbench/push',
    ])
  })
})

describe('agent track', () => {
  it('AGENT_REASON_TEXT covers every reason', async () => {
    const { AGENT_REASON_TEXT } = await import('./workbench')
    expect(Object.keys(AGENT_REASON_TEXT).sort()).toEqual(['checkpoint_unavailable', 'disabled',
      'invalid_citation', 'level_check', 'malformed', 'max_attempts', 'model_error', 'not_configured',
      'quota_exhausted', 'stage_budget', 'token_budget', 'tool_rounds_exceeded',
      // v2.5 §4.4 + D23 (same as backend agent.REASON_TEXT)
      'thesis_count', 'invalid_condition', 'changes_mismatch', 'no_change', 'review_malformed',
      'migration_026_missing', 'review_in_progress', 'plan_invalid',
      // v2.7 §6.4
      'retro_malformed', 'lesson_invalid', 'answer_malformed', 'migration_027_missing', 'discussion_busy',
      'draft_exists',
      // v2.8 §8.8
      'not_connected', 'model_unavailable',
      // v2.9 §5.5 + R3
      'invalid_entry_type', 'invalid_max_hold', 'stop_lowered', 'stop_above_close', 'active_chain',
      // v2.13–v2.15: researcher, portfolio manager, proposals and briefs
      'research_malformed', 'research_facts_count', 'research_kind', 'research_inference_unsupported',
      'research_refs_invalid', 'research_summary_length', 'research_blocked_shape',
      'pm_malformed', 'pm_summary_length', 'pm_levels_forbidden', 'pm_ranking_invalid', 'pm_refs_invalid',
      'proposal_malformed', 'proposal_source', 'proposal_title', 'proposal_length', 'proposal_refs_invalid',
      'proposal_duplicate_title', 'proposal_limit', 'brief_empty', 'brief_length', 'brief_acceptance_missing'].sort())
  })

  it('AGENT_REASON_TEXT has not_connected and model_unavailable (v2.8 §8.8, verbatim)', async () => {
    const { AGENT_REASON_TEXT } = await import('./workbench')
    expect(AGENT_REASON_TEXT).toMatchObject({ not_connected: 'ChatGPT 未连接', model_unavailable: '所选模型不可用' })
    expect(AGENT_REASON_TEXT.not_configured).toBe('未配置')
  })

  it('AGENT_REASON_TEXT has the six new reasons', async () => {
    const { AGENT_REASON_TEXT } = await import('./workbench')
    expect(AGENT_REASON_TEXT).toMatchObject({
      retro_malformed: '复盘卡草稿格式无效', lesson_invalid: '教训草稿缺适用范围、反例或挂靠无效',
      answer_malformed: '回答格式无效', migration_027_missing: '迁移 027 未执行',
      discussion_busy: '上一个问题还在回答', draft_exists: '这张卡已有成功的草稿',
    })
  })

  it('AGENT_REASON_TEXT carries the v2.5 reasons verbatim', async () => {
    const { AGENT_REASON_TEXT } = await import('./workbench')
    expect(AGENT_REASON_TEXT).toMatchObject({
      thesis_count: '论点数量不符', invalid_condition: '失效或复查条件无效',
      changes_mismatch: '变化说明与实际差异不符', no_change: '修订与上版没有差异',
      review_malformed: '复查输出格式无效', migration_026_missing: '迁移 026 未执行',
      review_in_progress: '正在复查', plan_invalid: '修订版按规则作废',
    })
  })

  it('plans, shadows and discipline trades carry the agent kind', async () => {
    const wb = await import('./workbench')
    const plan: Plan = { ...basePlan, kind: 'agent', plan_version: 'agent-1' }
    const shadow: PlanShadow = { id: 's1', ticker: 'NVDA', signal_date: '2026-10-22', kind: 'agent',
                                 plan_version: 'agent-1', status: 'ok', outcome: {
                                   status: 'expired', fill_date: null, fill_price: null, legs: [], r: null,
                                   return: null, spy_return: null } }
    const trade: DisciplineTrade = { trade_id: 't1', ticker: 'NVDA', track: 'agent', flags: [],
                                     immature: [], r: null }
    expect([plan.kind, shadow.kind, trade.track]).toEqual(['agent', 'agent', 'agent'])
  })
})


// --- three-track comparison and review pushes (P4 design §4, §6) ---

import { rText, TRACK_STATUS_TEXT } from './workbench'

it('TRACK_STATUS_TEXT covers every status', () => {
  expect(Object.keys(TRACK_STATUS_TEXT).sort()).toEqual(['closed', 'expired', 'missing', 'no_plan',
    'not_executable', 'not_executed', 'open', 'pass', 'pending'].sort())
  expect(TRACK_STATUS_TEXT.missing).toBe('缺失')
  expect(TRACK_STATUS_TEXT.not_executed).toBe('未执行')
})

it('TRACK_STATUS_TEXT_V2 adds voided and merged (tracks.TRACK_STATUS_TEXT_V2)', async () => {
  const { TRACK_STATUS_TEXT_V2 } = await import('./workbench')
  expect(TRACK_STATUS_TEXT_V2).toEqual({ ...TRACK_STATUS_TEXT, voided: '开盘作废', merged: '并入其他链' })
})

it('rText formats sign and null', () => {
  expect(rText(1.5)).toBe('+1.50R')
  expect(rText(-0.25)).toBe('-0.25R')
  expect(rText(0)).toBe('+0.00R')
  expect(rText(null)).toBe('—')
})

describe('planKindText', () => {
  it('labels agent as the first version from review-6 / monthly-3 on and names agent_latest', async () => {
    const { planKindText } = await import('./workbench')
    expect(planKindText('agent', 'review-6')).toBe('Agent（首版）')
    expect(planKindText('agent', 'monthly-3')).toBe('Agent（首版）')
    // later calc versions keep the first-version meaning (v2.9 review-8 / monthly-4)
    expect(planKindText('agent', 'review-7')).toBe('Agent（首版）')
    expect(planKindText('agent', 'review-8')).toBe('Agent（首版）')
    expect(planKindText('agent', 'monthly-4')).toBe('Agent（首版）')
    expect(planKindText('agent_latest', 'review-8')).toBe('Agent（最新版）')
    expect(planKindText('agent', 'review-5')).toBe('Agent')
    expect(planKindText('agent', 'monthly-2')).toBe('Agent')
    expect(planKindText('agent_latest', 'review-6')).toBe('Agent（最新版）')
    expect(planKindText('rule', 'monthly-3')).toBe('规则')
    expect(planKindText('mystery', 'review-6')).toBe('mystery')
  })
})

describe('rule labels', () => {
  it('RULE_LABELS covers plan_stop and plan_target', () => {
    expect(RULE_LABELS.plan_stop).toBe('计划止损')
    expect(RULE_LABELS.plan_target).toBe('计划目标')
    expect(ruleLabel('plan_stop')).toBe('计划止损')
    // the original eight keep their wording
    expect(RULE_LABELS.trend_reclaim).toBe('趋势回踩修复')
    expect(Object.keys(RULE_LABELS)).toHaveLength(10)
  })
})


// --- v2.9 executable plans ---

const jsonRes = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const urls = () => (fetch as any).mock.calls.map((c: unknown[]) => String(c[0]))

describe('v2.9 texts (verbatim from the spec)', () => {
  it('entry types and opening cases', () => {
    expect(ENTRY_TYPE_TEXT).toEqual({ zone_limit: '区间限价', breakout_stop: '突破买入', next_open: '次日开盘' })
    expect(OPEN_CASE_TEXT).toEqual({ in_zone: '区间内', gap_up: '高开', gap_down: '低开' })
    expect(OPEN_SCENARIO_TEXT.in_zone).toBe('开在 {L}–{U}：开盘买入 {N} 股')
    expect(OPEN_SCENARIO_TEXT.gap_up).toEqual({
      zone_limit: '高开过 {U}：不追，挂 {U} 限价等回落', breakout_stop: '高开过 {U}：不追，挂 {U} 限价等回落',
      next_open: '高开过 {U}：不买，本计划作废' })
    expect(OPEN_SCENARIO_TEXT.gap_down).toEqual({
      zone_limit: '低开：≤ {S} 作废；否则回升到 {L} 时买入', breakout_stop: '低开：≤ {S} 作废；否则升破 {L} 时买入',
      next_open: '低开过 {L}：不买，本计划作废' })
  })

  it('fills L / U / S / N and shows an unknown share count as a dash', () => {
    const vars = { entry_low: '95.10', entry_high: '97.30', stop: '91.40', shares: 120 }
    expect(openCaseText('in_zone', 'zone_limit', vars)).toBe('开在 95.10–97.30：开盘买入 120 股')
    expect(openCaseText('gap_up', 'breakout_stop', vars)).toBe('高开过 97.30：不追，挂 97.30 限价等回落')
    expect(openCaseText('gap_down', 'zone_limit', vars)).toBe('低开：≤ 91.40 作废；否则回升到 95.10 时买入')
    expect(openCaseText('gap_down', 'next_open', vars)).toBe('低开过 95.10：不买，本计划作废')
    expect(openCaseText('in_zone', 'next_open', { ...vars, shares: null })).toBe('开在 95.10–97.30：开盘买入 — 股')
  })

  it('instructions and execution states', () => {
    expect(INSTRUCTION_TEXT).toEqual({
      hold: '继续持有', sell_half: '到 T1：卖出一半，余下按移动止损', exit: '次一交易日开盘卖出',
      no_plan: '无计划持仓（回撤规则看护）' })
    expect(EXECUTION_TEXT).toEqual({
      adopted: '已采纳（{date} 成交 {quantity} 股）', out_of_validity: '有效期外成交', pending: '入场期，未成交',
      not_executed: '未执行', executed: '已执行' })
    expect(executionText('adopted', { date: '10-31', quantity: 120 })).toBe('已采纳（10-31 成交 120 股）')
    expect(executionText('adopted')).toBe('已采纳')
    expect(executionText('pending')).toBe('入场期，未成交')
    expect(executionText('executed')).toBe('已执行')
    expect(executionText('unplanned')).toBe('计划外成交')
    expect(executionText(null)).toBe('')
  })

  it('the five new void reasons', () => {
    expect(AGENT_REASON_TEXT).toMatchObject({
      invalid_entry_type: '入场类型不在允许的三种之内', invalid_max_hold: '最长持有须为 5–20 个交易日',
      stop_lowered: '持仓期不能下移止损', stop_above_close: '持仓期止损不能高于最新收盘',
      active_chain: '已有进行中的计划链，这次触发已并入' })
  })

  it('409 templates are the spec sentences (R6, §4.7, R10), with the backend fillers as placeholders', () => {
    expect(CONFLICT_TEXT).toEqual({
      plan_stale: '成交时这条链已是第 {n} 版，请关联第 {n} 版',
      plan_after_fill: '这一版生成于成交之后，不可能是你照着做的那一版',
      chain_exiting: '这条链已发出退出指令，不再复查',
      active_chain: '{ticker} 已有进行中的计划链（{date} 第 {n} 版），这次触发已并入' })
    expect(JSON.stringify(CONFLICT_TEXT)).not.toContain('或选自定')
  })

  it('410 / 409 / 400 / 503 messages', () => {
    expect(DECISIONS_RETIRED_TEXT).toBe('decisions_retired: v2.9 起不再记录决定，采纳由成交推导；可在计划上写备注')
    expect(describeError(new WorkbenchError(DECISIONS_RETIRED_TEXT, 410)))
      .toBe('v2.9 起不再记录决定，采纳由成交推导；可在计划上写备注')
    expect(describeError(new WorkbenchError('decision_link_retired', 400))).toBe(DECISION_LINK_RETIRED_TEXT)
    expect(describeError(new WorkbenchError('migration_029_missing', 503))).toBe(MIGRATION_029_TEXT)
    expect(describeError(new WorkbenchError('迁移 029 未执行', 503))).toBe('迁移 029 未执行')
    expect(describeError(new WorkbenchError('plan_stale: 成交时这条链已是第 3 版，请关联第 3 版', 409)))
      .toBe('成交时这条链已是第 3 版，请关联第 3 版')
    expect(describeError(new WorkbenchError('plan_stale', 409))).toBe('成交时这条链已是第 — 版，请关联第 — 版')
    expect(describeError(new WorkbenchError('active_chain', 409))).toBe('已有进行中的计划链，这次触发已并入')
    expect(describeError(new WorkbenchError('plan_after_fill', 409))).toBe('这一版生成于成交之后，不可能是你照着做的那一版')
    expect(describeError(new WorkbenchError('chain_exiting', 409))).toBe(CONFLICT_TEXT.chain_exiting)
    expect(describeError(new WorkbenchError('active_chain: MU 已有进行中的计划链（10-29 第 2 版），这次触发已并入', 409)))
      .toBe('MU 已有进行中的计划链（10-29 第 2 版），这次触发已并入')
    expect(STALE_HOLDING_TEXT).toBe('今日持仓状态未写入（每日复查失败），以下为现算')
  })
})

describe('v2.9 holding line', () => {
  const h = { ticker: 'MU', quantity: 10, avg_cost: 90, last_close: 100, stop: 94.8, stop_mode: 'initial', target1: 108.1,
    t1_reached: false, to_stop_pct: -5.2, to_t1_pct: 8.1, held_sessions: 4, max_hold_sessions: 10, remaining_sessions: 6,
    r_now: 0.6, reached_1r: false, instruction: 'hold', exit_reason: null, reason: '无新信号，复查条件未满足' } as const
  it('follows the spec example', () => {
    expect(holdingLine(h as any)).toBe('MU 持有 4/10 天 · 距止损 −5.20% · 距 T1 +8.10% · 现 +0.6R · 继续持有：无新信号，复查条件未满足')
  })
  it('marks T1 reached, 1R reached, exit and no-plan holdings', () => {
    expect(holdingLine({ ...h, t1_reached: true, to_t1_pct: null, reached_1r: true } as any))
      .toContain('已触 T1 · 现 +0.6R · 已到 1R，时间止损解除')
    expect(holdingLine({ ...h, instruction: 'exit', reason: '止损触及' } as any)).toContain('次一交易日开盘卖出：止损触及')
    expect(holdingLine({ ...h, instruction: 'no_plan', to_stop_pct: null, reason: null } as any))
      .toBe('MU 持有 10 股 · 无计划持仓（回撤规则看护）')
    expect(holdingLine({ ...h, line: '后端拼好的一句' } as any)).toBe('后端拼好的一句')
  })
  it('formats signed percent points', () => {
    expect(signedPct(8.1)).toBe('+8.10%')
    expect(signedPct('-5.2')).toBe('−5.20%')
    expect(signedPct(null)).toBe('—')
  })
  it('names the plan a trade followed', () => {
    expect(tradePlanText({ plan_id: 'p1', plan_label: 'Agent MU·10-29 第 2 版', decision_id: null })).toBe(' · 关联计划：Agent MU·10-29 第 2 版')
    expect(tradePlanText({ plan_id: 'p1', decision_id: null })).toBe(' · 关联计划：p1')
    expect(tradePlanText({ plan_id: null, decision_id: 'd1' })).toBe(' · 关联旧决定')
    expect(tradePlanText({ plan_id: null, decision_id: null })).toBe('')
  })
})

describe('v2.9 api', () => {
  afterEach(() => vi.unstubAllGlobals())
  const stub = (body: unknown = {}) => vi.stubGlobal('fetch', vi.fn(async () => jsonRes(body)))

  it('getPlanTable sends session and include_extended only when set', async () => {
    stub()
    await getPlanTable()
    await getPlanTable('2026-10-29', true)
    expect(urls()).toEqual(['/api/workbench/plan-table', '/api/workbench/plan-table?session=2026-10-29&include_extended=true'])
  })

  it('getPlanHistory sends the two switches only when on', async () => {
    stub()
    await getPlanHistory('MU')
    await getPlanHistory('MU', { include_reviews: true, include_baselines: true })
    await getPlanHistory('MU', { include_baselines: true })
    expect(urls()).toEqual([
      '/api/workbench/instruments/MU/plan-history',
      '/api/workbench/instruments/MU/plan-history?include_reviews=true&include_baselines=true',
      '/api/workbench/instruments/MU/plan-history?include_baselines=true'])
  })

  it('holding status, linkable plans and notes', async () => {
    stub()
    await getHoldingStatus('2026-10-29')
    await getLinkablePlans('MU', '2026-10-29T14:00:00.000Z')
    await getPlanNotes({ ticker: 'MU', chain_signal_date: '2026-10-29' })
    await getPlanNotes({ plan_id: 'p1' })
    expect(urls()).toEqual([
      '/api/workbench/holding-status?session=2026-10-29',
      '/api/workbench/trades/linkable-plans?ticker=MU&executed_at=2026-10-29T14%3A00%3A00.000Z',
      '/api/workbench/plan-notes?ticker=MU&chain_signal_date=2026-10-29',
      '/api/workbench/plan-notes?plan_id=p1'])
  })

  it('posts notes and trades with the new body', async () => {
    stub()
    await postPlanNote({ request_id: 'r', body: '写给自己', ticker: 'MU', chain_signal_date: '2026-10-29' })
    await postTrade({ request_id: 'r', ticker: 'MU', side: 'buy', quantity: '5', price: '100', fee: '0',
      executed_at: '2026-10-29T14:00:00.000Z', note: null, plan_id: 'p1' })
    const calls = (fetch as any).mock.calls
    expect(calls[0][0]).toBe('/api/workbench/plan-notes')
    expect(calls[0][1].method).toBe('POST')
    expect(JSON.parse(calls[1][1].body)).toMatchObject({ plan_id: 'p1' })
    expect('decision_id' in JSON.parse(calls[1][1].body)).toBe(false)
  })

  it('no decision write remains in the client', async () => {
    const wb = await import('./workbench')
    expect('postDecision' in wb).toBe(false)
    expect('decisionPayload' in wb).toBe(false)
  })
})

describe('v2.9 row helpers', () => {
  it('renders 「已有活跃链，见 <TICKER>·<MM-DD>」 from the flag, the chain or inputs.active_chain', () => {
    const chain = { signal_date: '2026-10-29' } as any
    const base = (active_chain: unknown) => ({ ticker: 'MU', chain, baseline: { active_chain } as any })
    expect(activeChainText(base(true))).toBe('已有活跃链，见 MU·10-29')
    expect(activeChainText(base({ ticker: 'MU', signal_date: '2026-10-21' }))).toBe('已有活跃链，见 MU·10-21')
    expect(activeChainText({ ticker: 'MU', chain: null, baseline: { active_chain: true } as any })).toBe('已有活跃链')
    expect(activeChainText(base(false))).toBe('')
  })

  it('chain-level execution is 已执行, with what it followed', () => {
    expect(rowExecutionText({ execution: 'executed', execution_basis: '照 Agent 第 2 版' })).toBe('已执行（照 Agent 第 2 版）')
    expect(rowExecutionText({ execution: 'executed' })).toBe('已执行')
    expect(rowExecutionText({ execution: 'pending' })).toBe('入场期，未成交')
    expect(rowExecutionText({ execution: null })).toBe('')
  })
})
