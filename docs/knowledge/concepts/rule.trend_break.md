---
id: rule.trend_break
title: 跌破 MA50
group: rule
version: 1
applies_to: [rules-1]
summary: "卖出候选：此前至少连续 10 天收在 MA50 上方，当日收盘跌破 MA50。"
related: [rule.trend_reclaim, indicator.moving_average]
sources: [investment_assistant/analysis/rules.py, docs/archive/v2/features/daily-plan.md]
---

## 是什么

一只股票稳稳站在 50 日均线之上很久，突然收盘跌了下来，往往意味着中期趋势出现了破坏。这条规则把这种情形标成**卖出**候选，提醒你重新审视持仓。

## 怎么算 / 从哪来

信号日收盘（`adj_close`）满足：

- 此前连续至少 10 天（`days_above_ma50` = 10），每一天的收盘都高于当天自己的 MA50；
- 当日收盘 < MA50。

候选里冻结 `close`、`ma50`。均线口径见[均线](indicator.moving_average.md)。

## 在系统里哪里看到

- 「今日」决策单元的候选规则。「连续 10 天在 MA50 上方」没有冻结每日均线，「触发依据」里如实标「未冻结，无法复核」。

## 注意

- 规则本身不看你有没有持仓：没有持仓的卖出候选会被标记「回避」，同一天同时有买入候选则标「冲突」、默认观察，见冲突与回避。
- 前置条件同其它价格规则；规则**未经验证**（Phase 0 Gate HOLD）。
