---
id: plan.trailing_stop
title: 移动止损
group: plan
version: 1
applies_to: [plan-1]
summary: "触及 T1 后，止损改为 max(开仓以来最高收盘 − 3 ATR, 持仓成本)；每天收盘后更新、次日生效。"
related: [plan.stop, plan.target1, rule.plan_stop, indicator.atr14]
sources: [investment_assistant/execution/plan_levels.py, docs/archive/v2/features/daily-plan.md]
---

## 是什么

第一个目标到手之后，不想让已经赚到的利润再吐回去，所以止损从固定价位改成「跟着价格往上走」：价格创新高，止损线跟着抬高，但永远不低于你的成本。

## 怎么算 / 从哪来

- 触发条件：开仓以来，某个**早于评估日**的交易日最高价曾 ≥ [目标 T1](plan.target1.md)。
- 之后的当前止损 = max(开仓以来、评估日之前的最高收盘 − 3 × ATR, 持仓平均成本)；3 是计划里冻结的 `trail_atr_multiple`，ATR 用计划生成时的值。
- 因为只看评估日**之前**的收盘，所以止损在每天收盘后更新、次日才生效，不会拿当天还没收盘的价格去判断当天。
- 触及判定与初始止损相同：最低价 ≤ 当前止损，由[计划止损](rule.plan_stop.md)产生卖出候选，候选里的 `mode` 为 `trailing`。

## 在系统里哪里看到

- 「今日」有计划的持仓；推导视图的触发段。

## 注意

- 它只对有计划的持仓生效；没有计划的持仓用[持仓回撤](rule.position_drawdown.md)。
- 有了保本下限，触及 T1 之后在止损价离场理论上不会亏本（不计费用，跳空穿价除外）。
- 规则**未经验证**（Phase 0 Gate HOLD）。
