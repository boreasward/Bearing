---
id: rule.plan_stop
title: 计划止损
group: rule
version: 1
applies_to: [rules-1]
summary: "卖出候选，仅限有计划的持仓：信号日最低价触及或跌破当前止损（初始止损，或触及 T1 后的移动止损）。"
related: [plan.stop, plan.trailing_stop, rule.plan_target, rule.position_drawdown]
sources: [investment_assistant/analysis/rules.py, investment_assistant/execution/exits.py, docs/archive/v2/features/daily-plan.md]
---

## 是什么

你按计划买入之后，计划里的止损价就成了这笔持仓的离场线。这条规则每天检查最低价有没有碰到它，碰到就产生**卖出**候选。

## 怎么算 / 从哪来

- 只对**有计划的持仓**生效：取当前未平仓批次里最近一笔、且其决定关联了计划的买入所对应的计划。没有计划的持仓用[持仓回撤](rule.position_drawdown.md)。
- 条件：信号日最低价 ≤ 当前止损。
- 当前止损有两种：触及 T1 之前是计划的初始[止损](plan.stop.md)；触及 T1 之后是[移动止损](plan.trailing_stop.md)。如果开仓后发生过拆股，计划的价位按拆股比例换算。
- 候选里冻结 `low`、`stop` 和当前处于哪种模式（`mode`：`initial` 或 `trailing`）。

## 在系统里哪里看到

- 「今日」有计划的持仓所在的决策单元；推导视图的触发段。

## 注意

- 触及后系统只给出候选，卖不卖、什么价卖由你决定并录入成交；跳空低开穿过止损时，实际成交价会比止损价更差。
- 阈值冻结在计划里，规则**未经验证**（Phase 0 Gate HOLD）。
