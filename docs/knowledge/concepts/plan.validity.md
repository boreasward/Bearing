---
id: plan.validity
title: 有效期
group: plan
version: 2
applies_to: [plan-1, plan-2, agent-3, timeline-2]
summary: "规则计划在信号日后第 1 至第 3 个交易日内有效；过了有效期还没成交，计划到期作废。"
related: [plan.earnings, plan.status, agent.version_chain, review.plan_shadow]
sources: [investment_assistant/execution/plan_levels.py, docs/archive/v2/features/daily-plan.md]
---

## 是什么

计划是在信号日收盘后生成的，行情每天都在变，所以不能无限期有效。有效期规定了你可以在哪几个交易日里按这份计划入场。

## 怎么算 / 从哪来

- 有效期从信号日 T 之后第 1 个交易日（T+1）到第 3 个交易日（T+3），按美股交易日历数，周末和休市日不算。
- 计划里记录 `valid_from` 和 `valid_until`，过了 `valid_until` 还没成交就到期。
- 有效期内有财报，计划整份作废，见[财报规则](plan.earnings.md)。

## 在系统里哪里看到

- 「今日」计划块的有效期；「标的」页 K 线上的计划线段只画在有效期内。
- 计划影子里没成交的计划记为到期，见[计划影子](review.plan_shadow.md)。

## Agent 链的入场期上限（v2.9）

Agent 计划是一条随复查更新的版本链，每一版的有效期不同：首版 T+1 至 T+3；复查产出的修订版 S+1 至 S+3（S 是复查交易日）。入场期里整条链还有一个**入场期上限**：从前只是首版信号日后第 10 个交易日，v2.9 起改为**最近一次触发后第 10 个交易日**——首版信号日算一次触发，之后每次[再次触发](agent.retrigger.md)都重新起算，修订版的有效期不超过这个上限（上限写进新版的 `inputs.chain_entry_cap` 与 `chain_retriggers.entry_cap`）。持仓期的修订版不设上限。入场期最后一个有效日（或上限那天）再触发时，链是续期而不是过期。

## 注意

- `plan-1` 与 `plan-2` 的规则计划有效期相同（T+1 至 T+3）；Agent 版本链的有效期规则见上一节与[版本链](agent.version_chain.md)。
- 规则**未经验证**（Phase 0 Gate HOLD）。
