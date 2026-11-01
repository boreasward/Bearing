---
id: review.execution_status
title: "执行状态与备注"
group: review
version: 1
applies_to: [execution-1]
summary: "没有「采纳」按钮：成交关联了某一版计划就是采纳。一份计划版本的执行状态有已采纳、有效期外成交、等待成交、未执行四种，没有关联计划的买入记为计划外；备注只给你自己看。"
related: [system.decision, plan.own, review.version_basis, review.discipline_unplanned, review.retro_card, system.active_chain]
sources: [investment_assistant/execution/adoption.py, investment_assistant/execution/ledger.py, investment_assistant/views/plan_table.py, docs/features/review.md]
---

## 是什么
v2.9 起「今日」没有采纳 / 否决 / 降级的按钮：你的交易日志本身就是采纳的证据。录入成交时把它**关联到当时照着做的那一份计划**（或写自定价位，或选「无计划」），系统由成交推出每份计划的执行状态。

## 怎么算 / 从哪来
纯函数 `execution.status`（口径 `execution-1`），对计划的每一版：

| 状态 | 判定 | 页面 |
| --- | --- | --- |
| `adopted` 已采纳 | 有关联到这一版的未作废买入，成交的纽约日期在这一版的有效期内 | 已采纳（10-31 成交 120 股） |
| `out_of_validity` 有效期外成交 | 关联到这一版，但成交日在有效期之外 | 有效期外成交 |
| `pending` 等待成交 | 没有关联成交，还没到截止日，链仍在入场期 | 入场期，未成交 |
| `not_executed` 未执行 | 没有关联成交，有效期已过或链已结束 | 未执行 |

- **链级**：链上有任一关联买入（包括关联到被并入单元的规则基线）就是 `executed` 已执行，并写明照什么执行——Agent 第 n 版、规则基线或自定。
- **计划外**：买入成交没有关联任何计划（旧成交再看它的旧决定有没有带计划）。它没有计划可对照，不进纪律的遵守率分母，复盘卡里单列。
- **关联哪一版**：只能关联成交那一刻的链头——补录昨天的成交时，关联的仍是「照着做时看到的那一版」（见[决定时看到的版本](review.version_basis.md)）；比它旧的版本被拒（`plan_stale`），生成于成交之后的版本也被拒（`plan_after_fill`）。退出期的链不再提供关联。选「自定」会在同一个事务里写一份自定计划（见[自定计划](plan.own.md)）。
- **备注**：可以给一条链或一份非链计划写 1–1000 字备注（接口支持更正与撤回，页面暂未提供），显示在计划表、计划页、计划历史和复盘卡上。备注是写给自己的日志，**不进任何 Agent 提示词、不触发复查**；想让 Agent 参考，请用「我的补充」。备注不影响执行状态。

## 在系统里哪里看到
「今日」计划表每行的状态列；计划页版本链的执行状态；「复盘 → 交易与持仓」的关联计划；[复盘卡](review.retro_card.md)的「你的执行」；周度复盘的「按执行」分组。

## 注意
旧决定仍只读保留，旧链照旧按旧决定终止（`closed_by_decision`），见[决定与执行依据](system.decision.md)。采纳只说明你按这份计划做了，不说明计划有效；所有规则和 Agent 仍**未经验证**。
