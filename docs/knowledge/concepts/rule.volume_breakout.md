---
id: rule.volume_breakout
title: 放量突破
group: rule
version: 1
applies_to: [rules-1]
summary: "买入候选：收盘创前 20 日收盘新高，成交量不低于前 50 日均量的 1.5 倍，且收盘在 MA50 之上。"
related: [rule.trend_reclaim, indicator.high_low, indicator.volume_ratio, indicator.moving_average]
sources: [investment_assistant/analysis/rules.py, docs/archive/v2/features/daily-plan.md]
---

## 是什么

价格冲出近期区间并伴随明显放大的成交量，常被当作新一轮上涨的起点。这条规则把这种「放量突破」的股票挑出来，动作是**买入**。

## 怎么算 / 从哪来

信号日同时满足：

- 收盘（`adj_close`）高于**前 20 个交易日**的最高收盘（不含当天）；
- 当日成交量 ≥ 前 50 个交易日平均成交量 × 1.5（`volume_multiple` = 1.5，50 日不含当天）；
- 收盘 > MA50。

候选里冻结 `close`、`prior_high`、`volume`、`avg_volume_50`、`ma50`。口径细节见[区间高低点](indicator.high_low.md)和[量比与均量](indicator.volume_ratio.md)。

## 在系统里哪里看到

- 「今日」决策单元的候选规则与「触发依据」；每日筛选的「触发组」也以它（和[趋势回踩修复](rule.trend_reclaim.md)）为准。

## 注意

- 前置条件同其它价格规则：当日有日线、至少 200 根、复权因子无异常，否则记跳过原因。
- 突破有可能是假突破；系统不判断突破后的走势。阈值固定、没有调参，规则**未经验证**（Phase 0 Gate HOLD）。
