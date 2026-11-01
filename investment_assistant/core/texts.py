"""The Chinese text tables the backend and frontend must agree on (spec §4.2).

Cut verbatim from ``labels.py``, ``regime.LABEL_TEXT``, ``agent/__init__.REASON_TEXT``,
``run_overview.REASON_TEXT``, ``paper_strategies.REJECT_TEXT`` and ``paper_fill`` in
Task 0; the origin modules keep same-named re-exports until Task 1, and
``notify/templates.py`` imports only from here afterwards. The frontend mirrors live in
``web/src/lib/workbench.ts`` and are pinned by the cross-language tests.

Names here are unambiguous across the whole backend: the two ``REASON_TEXT`` tables of
the origin modules become ``AGENT_REASON_TEXT`` (agent reasons) and ``RUN_REASON_TEXT``
(run-overview reason / degradation texts), the regime labels become ``REGIME_LABEL_TEXT``."""
from __future__ import annotations

RULE_TEXT: dict[str, str] = {
    'trend_reclaim': '趋势回踩修复',
    'volume_breakout': '放量突破',
    'earnings_gap_up': '财报跳空向上',
    'trend_break': '跌破 MA50',
    'earnings_gap_down': '财报跳空向下',
    'position_drawdown': '持仓回撤',
    'new_filing': '新财报',
    'abnormal_move': '异常波动',
    'plan_stop': '计划止损',
    'plan_target': '计划目标',
}

# Short form for chart markers: at most four characters each.
RULE_SHORT: dict[str, str] = {
    'trend_reclaim': '回踩',
    'volume_breakout': '突破',
    'earnings_gap_up': '跳空涨',
    'trend_break': '破位',
    'earnings_gap_down': '跳空跌',
    'position_drawdown': '回撤',
    'new_filing': '财报',
    'abnormal_move': '异动',
    'plan_stop': '止损',
    'plan_target': '目标',
}

ACTION_TEXT: dict[str, str] = {'buy': '买入', 'sell': '卖出', 'watch': '观察'}

REGIME_LABEL_TEXT: dict[str, str] = {'risk_on': '进攻', 'neutral': '中性', 'risk_off': '防守',
                                     'insufficient': '样本不足'}

# Agent reasons (was agent/__init__.REASON_TEXT); the frontend AGENT_REASON_TEXT has the same texts.
AGENT_REASON_TEXT: dict[str, str] = {
    'disabled': '已关闭',
    'not_configured': '未配置',
    'quota_exhausted': '额度用尽',
    'token_budget': '超出 token 上限',
    'stage_budget': '超出时限',
    'max_attempts': '已达重试上限',
    'malformed': '输出格式无效',
    'invalid_citation': '引用无效已作废',
    'level_check': '价位校验未通过',
    'tool_rounds_exceeded': '工具调用超限',
    'model_error': '模型调用失败',
    'checkpoint_unavailable': '状态库未就绪',
    # agent-2 / daily review (v2.5 spec §4.4)
    'thesis_count': '论点数量不符',
    'invalid_condition': '失效或复查条件无效',
    'changes_mismatch': '变化说明与实际差异不符',
    'no_change': '修订与上版没有差异',
    'review_malformed': '复查输出格式无效',
    'migration_026_missing': '迁移 026 未执行',
    'review_in_progress': '正在复查',
    'plan_invalid': '修订版按规则作废',      # v2.5 D23: an entry-phase revision whose plan row is invalid
    # v2.7 growth loop (spec §6.4, verbatim; the frontend AGENT_REASON_TEXT has the same texts)
    'retro_malformed': '复盘卡草稿格式无效',
    'lesson_invalid': '教训草稿缺适用范围、反例或挂靠无效',
    'answer_malformed': '回答格式无效',
    'migration_027_missing': '迁移 027 未执行',
    'discussion_busy': '上一个问题还在回答',
    'draft_exists': '这张卡已有成功的草稿',
    # v2.8 ChatGPT connection (spec §8.8, verbatim; the frontend AGENT_REASON_TEXT has the same texts)
    'not_connected': 'ChatGPT 未连接',
    'model_unavailable': '所选模型不可用',
    # v2.9 agent-3 (spec §5.5, verbatim; R3 adds stop_above_close; the frontend AGENT_REASON_TEXT has the same texts)
    'invalid_entry_type': '入场类型不在允许的三种之内',
    'invalid_max_hold': '最长持有须为 5–20 个交易日',
    'stop_lowered': '持仓期不能下移止损',
    'stop_above_close': '持仓期止损不能高于最新收盘',
    'active_chain': '已有进行中的计划链，这次触发已并入',
}

# Run-overview reason / degradation-kind texts (was run_overview.REASON_TEXT) that AGENT_REASON_TEXT does
# not have (spec §7.4, D28); a code found in neither table is shown as is. The last block answers the
# per-rule skip reasons `<rule_id>:<reason>`: every value `rules.precondition` returns, plus
# `earnings_time_unknown`.
RUN_REASON_TEXT: dict[str, str] = {
    'not_trading_day': '纽约休市',
    'explicit_pool': '指定股票运行，不筛选',
    'manual_run': '手动运行不推送',
    'already_pushed': '今天已推送过',
    'task_disabled': '推送任务已关闭',
    'not_reached': '运行提前结束，未执行到',
    'already_completed': '已有结果',
    'already_reviewed': '今天已复查',
    'fresh_version': '当天刚生成，不复查',
    'object_cap': '超出复查对象上限 20',
    'atr_unavailable': 'ATR 无法计算',
    'draft_cap': '超出每次起草上限 5 张',
    'upstream_failed': '上游节点失败，未执行',
    'not_due': '今天没有到期的复盘',
    'daily_running': '另一次运行正在进行',
    # degradation kinds
    'fred_not_configured': 'FRED 未配置',
    'label_insufficient': '广度样本不足',
    'macro_not_ok': '宏观序列不完整',
    'conflict': '与已存记录不一致（未覆盖）',
    'universe_load_failed': '范围文件加载失败',
    'benchmark_missing': 'QQQ 缺当日数据',
    'derivation_mismatch': '推导核对不一致',
    'derivation_check_error': '推导核对出错',
    'revision_failed': '修订未通过校验',
    'agent_unavailable': 'Agent 不可用',
    'draft_rejected': '草稿未通过校验',
    # rule skip reasons
    'missing_signal_bar': '缺信号日日线',
    'insufficient_history': '日线不足 200 根',
    'adjustment_anomaly': '复权因子异常',
    'earnings_time_unknown': '财报时点未知',
}

# Paper-strategy order rejections (was paper_strategies.REJECT_TEXT).
REJECT_TEXT: dict[str, str] = {'schema_invalid': '订单格式无效', 'plan_not_buyable': '计划不可买入',
                               'chain_consumed': '计划链已使用', 'duplicate_order': '重复订单',
                               'exceeds_max_shares': '超过计划股数上限', 'max_positions': '达到持仓数量上限',
                               'insufficient_cash': '现金不足', 'no_position': '无可卖持仓',
                               'cannot_halve': '持仓不足以卖出一半', 'order_not_pending': '订单已不在待成交状态'}

# Paper-order cancellation / void / chain-end texts (was paper_fill.py).
CANCEL_REASON_TEXT: dict[str, str] = {'superseded_by_revision': '计划出了新版本，旧订单撤销',
                                      'chain_ended': '计划链已结束，订单撤销',
                                      'agent_cancel': 'Agent 主动撤单', 'validity_expired': '计划有效期已过',
                                      'account_closed': '账户已关闭，订单撤销'}
VOID_REASON_TEXT: dict[str, str] = {'gap_below_stop': '开盘不高于止损，计划作废', 'gap_above_zone': '开盘高于入场区间，计划作废',
                                    'gap_below_zone': '开盘低于入场区间，计划作废'}
CHAIN_DONE_TEXT: dict[str, str] = {'entry_voided': '入场作废，计划链结束', 'exited': '已全部卖出，计划链结束'}
