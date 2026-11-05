---
id: plan.stop
title: 止损
group: plan
version: 1
applies_to: [plan-1]
summary: "参考价减 2 倍 ATR14，四舍五入到分；之后任一交易日最低价触及即按止损处理。"
related: [indicator.atr14, plan.ref_price, plan.trailing_stop, review.r_multiple, rule.plan_stop]
sources: [investment_assistant/execution/plan_levels.py, investment_assistant/execution/exits.py, docs/archive/v2/features/daily-plan.md]
---

## 是什么

止损是你为这笔交易事先认定「判断错了」的价位：触及就认赔离场，把单笔亏损控制在可接受的范围内。它同时是整份计划风险的度量，仓位和[R](review.r_multiple.md)都以它为准。

## 怎么算 / 从哪来

- 止损 = 参考价 − 2 × [ATR14](indicator.atr14.md)，四舍五入到分。
- 因此每股风险 = 参考价 − 止损 = 2 个 ATR；[建议仓位](plan.sizing.md)按它反推股数。
- 触及判定：持仓后的任一交易日，**最低价 ≤ 当前止损**即触及，由[计划止损](rule.plan_stop.md)产生卖出候选。当前止损一开始就是这个价位，触及 T1 后改用[移动止损](plan.trailing_stop.md)。
- 影子模拟里，同一天既触及止损又触及目标时先按止损算；开盘就跳空穿过止损时按开盘价算，见[计划影子](review.plan_shadow.md)。

## 在系统里哪里看到

- 「今日」计划块的「止损 z」；「标的」页 K 线上的止损线；推导视图逐项计算里的止损公式。

## 注意

- 止损是一个价位，不是保证：隔夜跳空、停牌时成交价可能远差于止损。
- 拆股后，计划的价位按拆股比例换算后再与行情比较。
- 规则**未经验证**（Phase 0 Gate HOLD）。
