---
id: rule.trend_reclaim
title: 趋势回踩修复
group: rule
version: 1
applies_to: [rules-1]
summary: "买入候选：处于中长期上升趋势的股票，回踩 MA20 下方至少 3 天后重新站上 MA20。"
related: [rule.volume_breakout, rule.trend_break, indicator.moving_average]
sources: [investment_assistant/analysis/rules.py, investment_assistant/derivation/chain.py, docs/archive/v2/features/daily-plan.md]
---

## 是什么

一只股票长期趋势向上，短期回调了几天，今天又涨回短期均线之上——这条规则把它挑出来作为「回踩后修复」的买入候选。它的想法是：在上升趋势里，回调之后重新走强是比追高更有性价比的入场点。动作是**买入**。

## 怎么算 / 从哪来

信号日收盘（`adj_close`）同时满足：

- 收盘 > MA50 > MA200（中长期趋势向上）；
- 收盘 > MA20（重新站上短期均线）；
- 此前连续至少 3 天收在各自当日的 MA20 下方（`days_below_ma20` = 3）。

均线口径见[均线](indicator.moving_average.md)。候选里冻结 `close`、`ma20`、`ma50`、`ma200`。

## 在系统里哪里看到

- 「今日」决策单元的候选规则；展开「触发依据」逐条核对条件。「连续 3 天在 MA20 下方」这一条没有冻结每日均线，页面如实标「未冻结，无法复核」。
- 买入候选在无冲突、未决定时，会进一步生成规则计划，见决策单元。

## 注意

- 前置条件：当日有日线、至少 200 根日线、复权因子无异常；否则不产出候选，并记录跳过原因（`missing_signal_bar` / `insufficient_history` / `adjustment_anomaly`），不会写成「无信号」。
- 阈值事先固定，没有用历史数据调参；改阈值就发新版本，旧候选保留原版本和输入。
- 规则**未经验证**（Phase 0 Gate HOLD）：它只是研究候选，不是买入建议。
