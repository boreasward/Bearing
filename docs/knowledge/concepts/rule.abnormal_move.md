---
id: rule.abnormal_move
title: 异常波动
group: rule
version: 1
applies_to: [rules-1]
summary: "观察候选：当日涨跌幅绝对值达到近 20 日波动率的 2.5 倍，且当日没有新财报。"
related: [indicator.volatility_20, rule.new_filing, indicator.gap, system.decision_unit]
sources: [investment_assistant/analysis/rules.py, investment_assistant/derivation/chain.py, docs/archive/v2/features/daily-plan.md]
---

## 是什么

没有明显消息，价格却一天里动得远超平时，值得你去看看发生了什么。这条规则产生**观察**候选，提醒你关注，不建议买卖。

## 怎么算 / 从哪来

- 当日收益（全复权收盘价的日涨跌幅）不为 0，且其绝对值 ≥ 2.5 × 近 20 日波动率（`sigma` = 2.5；波动率口径见[近 20 日波动率](indicator.volatility_20.md)）。
- 同一天出现新的财报申报时不触发，让位给[新财报](rule.new_filing.md)。
- 候选里冻结 `return` 与 `stdev_20`。

## 在系统里哪里看到

- 「今日」决策单元里的候选规则与「触发依据」。

## 注意

- 前置条件同其它价格规则：当日有日线、至少 200 根日线、复权因子无异常。
- 方向不分涨跌；它也是 Agent 复查链时的一项系统信号（`abnormal_move`），见[系统信号](agent.signals.md)。
- 阈值固定、没有调参；规则**未经验证**（Phase 0 Gate HOLD）。
