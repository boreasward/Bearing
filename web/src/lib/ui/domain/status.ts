// The one status vocabulary of the site (spec 4.4): { domain, key } -> { Chinese label, tone }.
// `StatusPill` only knows this table. To add a status: add the key to TONES (one line per domain, no spreads;
// status.test.ts checks the shape), add its label to LABELS, then use it.

export type Tone = 'success' | 'warn' | 'danger' | 'info' | 'running' | 'neutral'
export type StatusDomain =
  | 'node' | 'run' | 'chain' | 'fetch' | 'agent' | 'connection' | 'card' | 'retro' | 'push'
  | 'execution' | 'knowledge' | 'instruction' | 'system' | 'block' | 'consistency'
  | 'account' | 'paper_order' | 'paper_decision' | 'reconcile'
  | 'research_request' | 'proposal' | 'portfolio_verdict' | 'data_request' | 'feature_flag' | 'roadmap_version'
  | 'plan' | 'plan_action' | 'stop_order'
  | 'research' | 'retro_tag' | 'rotation' | 'rotation_state' | 'regime' | 'etf_state' | 'write' | 'daily_run' | 'tool_write'

// Keep every domain on ONE line, without spreads: status.test.ts reads this block by lines.
export const TONES = {
  regime: { risk_on: 'success', neutral: 'neutral', risk_off: 'warn', insufficient: 'warn' },
  etf_state: { strong: 'success', mixed: 'neutral', weak: 'neutral' },
  write: { written: 'success', preview: 'warn', replayed: 'neutral' },
  daily_run: { final: 'success', draft: 'warn', error: 'danger', unfinished: 'warn', pending: 'neutral', running: 'running' },
  tool_write: { direct: 'neutral', confirm: 'neutral' },
  // stage_runs.status (+ pending / running derived by the page) and the run overview's missing / not_recorded stage lines
  node: { success: 'success', partial: 'warn', degraded: 'warn', failed: 'danger', skipped: 'neutral', pending: 'neutral', running: 'running', missing: 'warn', not_recorded: 'neutral' },
  // job_reports.status (success / degraded / error) plus the node values
  run: { success: 'success', partial: 'warn', degraded: 'warn', failed: 'danger', skipped: 'neutral', pending: 'neutral', running: 'running', error: 'danger' },
  // timeline.CHAIN_STATES: only entry / holding / exiting are live; every ended state is neutral
  chain: { entry: 'info', holding: 'success', exiting: 'warn', legacy: 'neutral', invalidated: 'neutral', closed_by_decision: 'neutral', position_closed: 'neutral', superseded: 'neutral', expired: 'neutral' },
  // data_fetch_runs.status (+ running / error in the family cards)
  fetch: { success: 'success', partial: 'warn', failed: 'danger', skipped: 'neutral', running: 'running', error: 'danger' },
  // agent.STATUSES
  agent: { ok: 'success', pass: 'neutral', rejected: 'danger', error: 'danger', skipped: 'neutral' },
  // agent_connections.status (+ no_sharing / unknown reported by the connection view)
  connection: { connected: 'success', pending: 'running', needs_login: 'danger', disconnected: 'neutral', no_sharing: 'warn', unknown: 'neutral' },
  // retro.CARD_STATUSES
  card: { reviewed: 'success', to_review: 'warn', drafting: 'warn', draft_failed: 'danger' },
  // weekly / monthly retro run results (RETRO_STATUS_TEXT)
  retro: { reviewed: 'success', generated: 'success', to_review: 'warn', drafting: 'warn', draft_failed: 'danger', error: 'danger', reused: 'neutral', already_sent: 'neutral', reminded: 'info' },
  // RETRO_PUSH_TEXT (other "not sent" reasons are shown as they come)
  push: { sent: 'success', manual_run: 'neutral', not_sent: 'neutral' },
  // execution.STATUSES | CHAIN_STATUSES | retro.EXECUTION_STATUSES
  execution: { adopted: 'success', executed: 'success', pending: 'info', unplanned: 'warn', out_of_validity: 'neutral', not_executed: 'neutral' },
  // knowledge_status.status
  knowledge: { active: 'success', archived: 'neutral' },
  // timeline.INSTRUCTION_TEXT
  instruction: { hold: 'neutral', sell_half: 'warn', exit: 'danger', no_plan: 'info' },
  // services/health.py check status (up / degraded / down / unknown)
  system: { up: 'success', degraded: 'warn', down: 'danger', unknown: 'neutral' },
  // DataBlock / MetricValue / SampleSize / CutoffTime
  block: { not_computed: 'warn', insufficient: 'warn' },
  // derivation.CONSISTENCY_TEXT (plan derivation check)
  consistency: { consistent: 'success', mismatch: 'danger', incomplete: 'warn' },
  account: { active: 'success', closed: 'neutral' },
  paper_order: { pending: 'info', filled: 'success', voided: 'neutral', expired: 'neutral', cancelled: 'neutral', rejected: 'danger' },
  paper_decision: { ok: 'success', skipped: 'neutral', rejected: 'danger' },
  reconcile: { matched: 'success', mismatch: 'danger', no_ledger: 'neutral' },
  research_request: { queued: 'info', running: 'running', waiting_data: 'warn', concluded: 'success', closed: 'neutral', error: 'danger' },
  proposal: { proposed: 'info', approved: 'success', rejected: 'neutral', superseded: 'neutral' },
  portfolio_verdict: { ok: 'success', blocked: 'danger', deferred: 'warn' },
  data_request: { queued: 'info', fetched: 'success', failed: 'danger', deferred: 'warn' },
  feature_flag: { enabled: 'success', disabled: 'neutral' },
  roadmap_version: { planned: 'info', in_progress: 'running', engineering_done: 'success', user_acceptance_pending: 'warn', done: 'success', dropped: 'neutral' },
  // v3.1 计划线七态（接口 /api/plans 的 status）：pending / open 进行中，五种已结束状态全部中性
  plan: { pending: 'info', open: 'success', closed: 'neutral', expired: 'neutral', invalidated: 'neutral', replaced: 'neutral', cancelled: 'neutral' },
  // 「下一个交易日的操作」五种（spec §7.4 的 next_action.kind）：前两种警示语气，后三种常规
  plan_action: { stop_hit_unresolved: 'danger', sell_next_open: 'warn', rotation_sell: 'warn', unchecked: 'info', stop_order: 'info', hold: 'neutral' },
  // 持仓的止损挂单状态（stop_order_status）
  stop_order: { none: 'neutral', needs_raise: 'warn', confirmed: 'success' },
  // v3.2 研究五态（GET /api/research 的 status）：观察中唯一进行中，四个终态全部中性，已转计划例外是 success
  research: { watching: 'info', planned: 'success', rejected: 'neutral', expired: 'neutral', dropped: 'neutral' },
  // v3.2 复盘偏差标签（execution.retrospect 的 tag.code）+ 照做：not_filled 不算偏差是 neutral，七个偏差全部 warn
  retro_tag: { followed: 'success', not_filled: 'neutral', not_bought_in_zone: 'warn', entry_out_of_zone: 'warn', over_bought: 'warn', stop_order_missing: 'warn', late_sell_after_stop: 'warn', late_sell_after_instruction: 'warn', early_sell: 'warn', over_cap: 'warn' },
  // v3.3 换仓比较结论（rotation.verdict）：继续持有中性，半仓换 / 全仓换要动作所以信息语气
  rotation: { hold: 'neutral', half: 'info', full: 'info' },
  // v3.3 换仓指令的执行情况（rotation record.execution.state）：待执行 info、已执行 success，其余中性
  rotation_state: { pending: 'info', done: 'success', withdrawn: 'neutral', superseded: 'neutral' },
} as const satisfies Record<StatusDomain, Record<string, Tone>>

// `execution.adopted` keeps its `{date}` / `{quantity}` placeholders: pass the filled sentence as StatusPill `text`.
export const LABELS: Record<StatusDomain, Record<string, string>> = {
  regime: { risk_on: '进攻', neutral: '中性', risk_off: '防守', insufficient: '样本不足' },
  etf_state: { strong: '强', mixed: '混合', weak: '弱' },
  write: { written: '已写入', preview: '待确认', replayed: '重放' },
  daily_run: { final: '成功', draft: '降级', error: '失败', unfinished: '未做完', pending: '待运行', running: '运行中' },
  tool_write: { direct: '写入', confirm: '写入 · 先确认' },
  node: { success: '成功', degraded: '降级', partial: '部分成功', failed: '失败', skipped: '跳过', pending: '未开始', running: '运行中', missing: '缺证据', not_recorded: '未记录' },
  run: { success: '成功', degraded: '降级', partial: '部分成功', failed: '失败', skipped: '跳过', pending: '未开始', running: '运行中', error: '失败' },
  chain: {
    entry: '入场期', holding: '持仓期', invalidated: '已作废', closed_by_decision: '已决定不买',
    position_closed: '已平仓', expired: '已到期', legacy: '旧版', exiting: '退出期', superseded: '被取代',
  },
  fetch: { success: '成功', partial: '部分成功', failed: '失败', skipped: '跳过', running: '运行中', error: '失败' },
  agent: { ok: '成功', pass: '不建议买入', rejected: '未通过校验', error: '出错', skipped: '跳过' },
  connection: {
    connected: '已连接', pending: '登录中', needs_login: '需要重新登录', disconnected: '未连接', no_sharing: '未开启共享', unknown: '未知',
  },
  card: { reviewed: '已审定', to_review: '待审定', draft_failed: '起草失败（可手动审定或重新起草）', drafting: '起草中' },
  retro: {
    to_review: '待审定', drafting: '起草中', draft_failed: '起草失败', reviewed: '已审定',
    generated: '已生成', reused: '沿用已冻结快照', already_sent: '已推送过', reminded: '已提醒', error: '出错',
  },
  push: { sent: '已推送', manual_run: '手动运行不推送', not_sent: '未发送' },
  execution: {
    adopted: '已采纳（{date} 成交 {quantity} 股）', out_of_validity: '有效期外成交', pending: '入场期，未成交',
    not_executed: '未执行', executed: '已执行', unplanned: '计划外成交',
  },
  knowledge: { active: '有效', archived: '已归档' },
  instruction: {
    hold: '继续持有', sell_half: '到 T1：卖出一半，余下按移动止损', exit: '次一交易日开盘卖出', no_plan: '无计划持仓（回撤规则看护）',
  },
  system: { up: '正常', degraded: '降级', down: '不可用', unknown: '未知' },
  block: { not_computed: '没算', insufficient: '样本不足' },
  consistency: { consistent: '推导与重算一致', mismatch: '推导与已存值不一致', incomplete: '推导不完整' },
  account: { active: '使用中', closed: '已关闭' },
  paper_order: { pending: '待成交', filled: '已成交', voided: '已作废', expired: '已过期', cancelled: '已撤单', rejected: '未通过校验' },
  paper_decision: { ok: '已决策', skipped: '跳过', rejected: '未通过校验' },
  reconcile: { matched: '对账一致', mismatch: '待核对', no_ledger: '暂无账本权益' },
  research_request: { queued: '排队中', running: '研究中', waiting_data: '等待数据', concluded: '已得结论', closed: '已结束', error: '出错' },
  proposal: { proposed: '待决定', approved: '已批准', rejected: '已驳回', superseded: '已取代' },
  portfolio_verdict: { ok: '可执行', blocked: '被拦截', deferred: '暂缓' },
  data_request: { queued: '排队中', fetched: '已补采', failed: '失败', deferred: '暂缓' },
  feature_flag: { enabled: '已开启', disabled: '已关闭' },
  roadmap_version: { planned: '已规划', in_progress: '实施中', engineering_done: '工程完成', user_acceptance_pending: '待用户验收', done: '已完成', dropped: '已放弃' },
  plan: { pending: '待入场', open: '已买入', closed: '已平仓', expired: '已过期', invalidated: '已作废', replaced: '被替换', cancelled: '已取消' },
  plan_action: { stop_hit_unresolved: '止损已触及，未见卖出记录', sell_next_open: '下次开盘卖出', unchecked: '缺日线，无法检查止损', stop_order: '未确认止损单', hold: '继续持有', rotation_sell: '换仓卖出' },
  stop_order: { none: '未挂止损单', needs_raise: '止损单需上调', confirmed: '已确认挂单' },
  research: { watching: '观察中', planned: '已转计划', rejected: '已否决', expired: '已过期', dropped: '已放弃' },
  // 带 N 的两个标签（晚卖 N 个交易日）由组件拼句后经 StatusPill 的 text 传入，表里存不带数字的文案
  retro_tag: { followed: '照做', not_filled: '未买入：价格未进入场区间', not_bought_in_zone: '未买入：价格进过入场区间', entry_out_of_zone: '买价出区间', over_bought: '多买', stop_order_missing: '止损未挂', late_sell_after_stop: '止损触及后晚卖', late_sell_after_instruction: '卖出指令后晚卖', early_sell: '早卖', over_cap: '买入后超出仓位上限' },
  rotation: { hold: '继续持有', half: '半仓换', full: '全仓换' },
  rotation_state: { pending: '待执行', done: '已执行', withdrawn: '已撤回', superseded: '被取代' },
}

export type StatusInfo = { label: string; tone: Tone; known: boolean }

/** Unknown keys throw in dev (so a new backend status cannot slip in unnoticed) and are neutral grey with the raw key in production. */
export function statusOf(domain: StatusDomain, key: string): StatusInfo {
  const tones = TONES[domain] as Record<string, Tone>
  if (Object.hasOwn(tones, key)) return { label: LABELS[domain][key] ?? key, tone: tones[key], known: true }
  if (import.meta.env.DEV) throw new Error(`unknown status: ${domain}.${key}`)
  return { label: key, tone: 'neutral', known: false }
}
