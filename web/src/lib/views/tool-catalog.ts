import type { ToolName } from './registry'
import { TOOL_LABELS } from './labels'

export type ToolWrite = 'none' | 'direct' | 'confirm'
export type ToolGroupKey = 'read' | 'plan' | 'research' | 'ledger' | 'watch'

export const TOOL_GROUPS: readonly { key: ToolGroupKey; label: string }[] = [
  { key: 'read', label: '看数据' },
  { key: 'plan', label: '计划与持仓' },
  { key: 'research', label: '研究' },
  { key: 'ledger', label: '账本' },
  { key: 'watch', label: '关注与数据' },
]

export const TOOL_CATALOG: Record<ToolName, { group: ToolGroupKey; write: ToolWrite; summary: string }> = {
  get_market: { group: 'read', write: 'none', summary: '当天的市场环境：区制、广度、SPY 20 日收益、板块 ETF 强弱与宏观指标。' },
  get_candidates: { group: 'read', write: 'none', summary: '当天的规则名单：排名、理由、形态与行业、是否已有结论或计划，以及掉出名单的股票。' },
  get_coverage: { group: 'read', write: 'none', summary: '各类数据当天的覆盖率与缺日线的股票，用来判断数据够不够用。' },
  get_price_bars: { group: 'read', write: 'none', summary: '某只股票最近若干根日线：开高低收与成交量。' },
  get_indicators: { group: 'read', write: 'none', summary: '某只股票当天的指标：收盘、均线、相对强度等。' },
  get_news: { group: 'read', write: 'none', summary: '某只股票最近几天的新闻，最多 20 条。' },
  get_filings: { group: 'read', write: 'none', summary: '某只股票最近的 SEC 申报，最多 20 条。' },
  get_earnings: { group: 'read', write: 'none', summary: '某只股票下一次财报日。' },
  get_fundamentals: { group: 'read', write: 'none', summary: '某只股票最近几个财季的基本面数字。' },
  get_positions: { group: 'read', write: 'none', summary: '当前持仓：数量、成本、浮动盈亏、止损与下一个交易日的操作。' },
  get_plans: { group: 'read', write: 'none', summary: '交易计划及其变化记录，可按状态、股票或编号筛选。' },
  get_account: { group: 'read', write: 'none', summary: '账户：现金、总资产、盈亏、最近一次对账差额与最近平仓结果。' },
  get_agenda: { group: 'read', write: 'none', summary: '当天待办：要复查的持仓、再次上榜的计划、待二次确认的股票与剩余名额。' },
  get_concept: { group: 'read', write: 'none', summary: '操作手册里的概念卡；不指定时给出目录。' },
  get_research: { group: 'read', write: 'none', summary: '研究记录及其时间线，可按状态、股票或编号筛选。' },
  get_rotation_table: { group: 'plan', write: 'none', summary: '换仓比较表：账户仓位占比与剩余额度、候选的价位与可买股数、各持仓的卖出方案与估计手续费；只看，不保存。' },
  compute_plan_levels: { group: 'plan', write: 'none', summary: '按规则算一只股票的入场区、止损、目标、有效期与股数；只计算，不保存。' },
  save_verdict: { group: 'plan', write: 'direct', summary: '保存对一只股票的二次确认结论（确认 / 否决 / 无法判断）；否决会作废它的待入场计划。' },
  save_plan: { group: 'plan', write: 'direct', summary: '给已确认的股票保存交易计划；股数由系统计算。' },
  continue_plan: { group: 'plan', write: 'direct', summary: '延续一份再次上榜的待入场计划：数字不变，有效期重算。' },
  revise_plan: { group: 'plan', write: 'direct', summary: '修订一份再次上榜的待入场计划：改价位、论点或作废条件，并写明原因。' },
  cancel_plan: { group: 'plan', write: 'confirm', summary: '取消一份待入场计划。' },
  save_holding_review: { group: 'plan', write: 'direct', summary: '保存一只持仓的复查结论；论点不成立时转成下次开盘卖出。' },
  raise_stop: { group: 'plan', write: 'confirm', summary: '把一只持仓的止损调高；只上调不下调。' },
  confirm_stop_order: { group: 'plan', write: 'confirm', summary: '确认你已在券商挂好止损单。' },
  save_rotation: { group: 'plan', write: 'confirm', summary: '保存一次换仓比较的结论（继续持有 / 半仓换 / 全仓换）；半仓与全仓会在持仓上记下换仓卖出指令。' },
  withdraw_rotation: { group: 'plan', write: 'confirm', summary: '撤回一只持仓上还没执行的换仓卖出指令。' },
  save_summary: { group: 'plan', write: 'direct', summary: '保存当天简报的一句话总结。' },
  start_research: { group: 'research', write: 'confirm', summary: '对你点名的股票发起一份研究，记下论点并开始跟踪。' },
  conclude_research: { group: 'research', write: 'confirm', summary: '给研究下结论：可以出计划 / 值得盯 / 否决；「值得盯」的新条件要你确认。' },
  drop_research: { group: 'research', write: 'confirm', summary: '放弃一份观察中的研究。' },
  record_trade: { group: 'ledger', write: 'confirm', summary: '记一笔成交；买入会关联计划并给出止损单指令，卖完给出平仓结果。' },
  record_cash_flow: { group: 'ledger', write: 'confirm', summary: '记一笔出入金：期初、入金、出金或调整。' },
  record_equity: { group: 'ledger', write: 'confirm', summary: '记一次对账：填券商显示的总资产，返回与系统算出的差额。' },
  void_record: { group: 'ledger', write: 'confirm', summary: '作废一笔录错的成交或出入金。' },
  watchlist_add: { group: 'watch', write: 'confirm', summary: '把一只股票加进关注列表。' },
  watchlist_remove: { group: 'watch', write: 'confirm', summary: '把一只股票移出关注列表。' },
  ensure_data: { group: 'watch', write: 'direct', summary: '给名单外的股票补齐日线、新闻、申报、财报日与基本面数据。' },
}

export function toolsByGroup() {
  return TOOL_GROUPS.map(({ key, label }) => ({
    key,
    label,
    tools: (Object.entries(TOOL_CATALOG) as [ToolName, (typeof TOOL_CATALOG)[ToolName]][])
      .filter(([, tool]) => tool.group === key)
      .map(([name, tool]) => ({ name, label: TOOL_LABELS[name], write: tool.write, summary: tool.summary })),
  }))
}
