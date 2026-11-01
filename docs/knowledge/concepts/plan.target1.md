---
id: plan.target1
title: 目标 T1
group: plan
version: 1
applies_to: [plan-1]
summary: "参考价加 4 倍 ATR14（相当于 2R）；持仓第一次触及后建议卖出一半，余下改用移动止损。"
related: [indicator.atr14, plan.stop, plan.trailing_stop, rule.plan_target, review.r_multiple]
sources: [investment_assistant/execution/plan_levels.py, investment_assistant/execution/exits.py, docs/features/daily-plan.md]
---

## 是什么

T1 是计划的第一个获利目标。它的距离是止损距离的两倍，也就是风险回报比 1 : 2：值得冒 1 份风险，换 2 份回报。

## 怎么算 / 从哪来

- 目标 T1 = 参考价 + 4 × [ATR14](indicator.atr14.md)，四舍五入到分。
- 止损距离是 2 个 ATR，所以 T1 恰好是 +2R（见[R](review.r_multiple.md)）。
- 持仓期间，某个交易日的最高价 ≥ T1，就是**触及 T1**：[计划目标](rule.plan_target.md)在第一次触及的那天产生「卖出一半」的候选，只触发一次；之后止损改为[移动止损](plan.trailing_stop.md)。

## 在系统里哪里看到

- 「今日」计划块的「T1 w」；「标的」页 K 线上的目标线。

## 注意

- 「一半」是建议比例，实际卖多少由你决定。
- 这个目标比较保守，只是第一个；系统没有第二个目标，余下仓位靠移动止损离场。
- 规则**未经验证**（Phase 0 Gate HOLD）：目标位只是算术结果，不是预测。
