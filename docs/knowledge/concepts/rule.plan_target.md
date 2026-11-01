---
id: rule.plan_target
title: 计划目标
group: rule
version: 1
applies_to: [rules-1]
summary: "卖出候选，仅限有计划的持仓：开仓以来第一次最高价触及 T1，建议卖出一半，只触发一次。"
related: [plan.target1, plan.trailing_stop, rule.plan_stop, rule.position_drawdown]
sources: [investment_assistant/analysis/rules.py, investment_assistant/execution/exits.py, docs/features/daily-plan.md]
---

## 是什么

计划里的第一个目标价（T1）到了，按计划先落袋一部分：这条规则在持仓第一次碰到 T1 的那天产生**卖出一半**的候选，剩下的仓位改由移动止损保护。

## 怎么算 / 从哪来

- 只对**有计划的持仓**生效，计划的选取同[计划止损](rule.plan_stop.md)。
- 条件：信号日最高价 ≥ [目标 T1](plan.target1.md)，且在此之前开仓以来的交易日里最高价从未触及过 T1。
- 候选里冻结 `high`、`target1` 和建议卖出比例 `sell_fraction` = 0.5。
- 因为判断「此前是否触及」用的是信号日之前的日线，所以每个持仓只会触发一次；触及后止损改为[移动止损](plan.trailing_stop.md)。

## 在系统里哪里看到

- 「今日」有计划的持仓所在的决策单元；推导视图的触发段。「目标一此前未触及」这一条依赖计划状态，页面如实标「未冻结，无法复核」。

## 注意

- 同一天可能既触及 T1 又触及止损，这时两条卖出候选会同时出现；候选如何合成建议动作见[决策单元](system.decision_unit.md)。
- 一半是建议比例，实际卖多少由你决定；规则**未经验证**（Phase 0 Gate HOLD）。
