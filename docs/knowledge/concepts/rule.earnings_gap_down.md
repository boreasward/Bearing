---
id: rule.earnings_gap_down
title: 财报跳空向下
group: rule
version: 1
applies_to: [rules-1]
summary: "卖出候选：8-K Item 2.02 财报发布后的第一个交易日，开盘较前收盘跳空 ≤ −3%，且收盘不高于开盘。"
related: [rule.earnings_gap_up, indicator.gap, family.sec_filings, system.conflict]
sources: [investment_assistant/analysis/rules.py, investment_assistant/derivation/chain.py, docs/archive/v2/features/daily-plan.md]
---

## 是什么

财报不及预期时，股价常在次日低开，并且当天没有收回来。这条规则把这样的股票标成**卖出**候选。它与[财报跳空向上](rule.earnings_gap_up.md)是同一个事件、方向相反。

## 怎么算 / 从哪来

- 事件与时点判断同[财报跳空向上](rule.earnings_gap_up.md)：8-K Item 2.02，按 SEC 受理时间换算，盘中发布不触发。
- 条件：该日开盘较前一日收盘的跳空 `gap` ≤ −3%（见[跳空幅度](indicator.gap.md)），且当日收盘 ≤ 开盘。
- 候选里冻结 `open`、`close`、`prev_close`、`gap`。

## 在系统里哪里看到

- 「今日」决策单元的候选规则与「触发依据」。

## 注意

- 没有持仓的卖出候选标记「回避」；同日同时有买入候选则标「冲突」，见[冲突与回避](system.conflict.md)。
- 受理时间缺失时只记 `earnings_time_unknown`，不猜；价格前置条件同其它规则。
- 规则**未经验证**（Phase 0 Gate HOLD）。
