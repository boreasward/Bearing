---
id: plan.ref_price
title: 参考价
group: plan
version: 1
applies_to: [plan-1]
summary: "规则计划所有价位的起点：信号日（T 日）的收盘价，生成时冻结；它不是让你下单的价格。"
related: [plan.entry_zone, plan.stop, plan.target1, indicator.atr14]
sources: [investment_assistant/execution/plan_levels.py, docs/archive/v2/features/daily-plan.md]
---

## 是什么

规则计划要给出入场区间、止损、目标，总得有一个起点。系统取信号日（也就是触发候选的那个交易日，记作 T 日）的**收盘价**作为参考价，其余价位都是「参考价 ± 若干个 ATR」。

## 怎么算 / 从哪来

- 参考价 = T 日收盘价（拆股调整、未分红调整的 `close`），保留到小数点后 6 位冻结在计划里。
- 之后的所有价位都由它和 [ATR14](indicator.atr14.md) 算出：[入场区间](plan.entry_zone.md)、[止损](plan.stop.md)、[目标 T1](plan.target1.md)。
- 仓位计算里的每股风险 = 参考价 − 止损。

## 在系统里哪里看到

- 计划块的字段 `ref_price`，推导视图「输入快照」里的参考价和它指向的那根日线。

## 注意

- 参考价是**昨天的**收盘，不是下一个交易日的开盘价，更不是下单价；计划在 T+1 至 T+3 个交易日里有效，见[有效期](plan.validity.md)。
- 自定计划和 Agent 计划也记录 T 日收盘作参考，但它们的仓位按各自入场区间的中点算。
