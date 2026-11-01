---
id: plan.context_flags
title: 市场环境提示
group: plan
version: 1
applies_to: [plan-1, context-2]
summary: "计划冻结当天的区制与行业状态，只加提示 regime_risk_off / regime_unavailable / sector_weak，不改价位、仓位和有效性。"
related: [indicator.regime, indicator.sector_strength, plan.status, plan.earnings]
sources: [investment_assistant/execution/plan_levels.py, investment_assistant/analysis/regime.py, docs/features/pool-and-context.md]
---

## 是什么

一份买入计划是在什么样的大环境下生成的，会冻结进计划里；其中不利的情形以提示的形式显示。它只帮你判断「现在是不是逆风」，本身不改变计划。

## 怎么算 / 从哪来

分层、区制、所属行业和下次财报日一起冻结在计划的 `inputs.context`（`context-2`）。提示按下表生成：

| 提示 | 条件 |
| --- | --- |
| `regime_risk_off` | 当天[区制](indicator.regime.md)为防守 |
| `regime_unavailable` | 当天没有可用的区制快照（快照缺失或失败） |
| `sector_weak` | 所属行业 ETF 的[强弱](indicator.sector_strength.md)状态为弱 |

## 在系统里哪里看到

- 计划块的提示列表；推导视图「输入快照」里的区制与行业。

## 注意

- **提示不改任何价位、仓位和计划有效性**——防守区制时计划仍然照常生成。
- 区制标签「样本不足」不会产生提示；没有映射行业的股票也没有行业提示。
- 区制与行业强弱的判定都**未经验证**（Phase 0 Gate HOLD）。
