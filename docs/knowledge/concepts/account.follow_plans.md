---
id: account.follow_plans
title: "照计划执行账户"
group: account
version: 1
applies_to: [account-1, follow-plans-1]
summary: "在现金与仓位上限内，程序对所有可买 Agent 计划按最大允许股数挂单；它为 Agent 的组合选择提供执行参照。"
related: [account.paper, account.agent_portfolio, account.fill_rules, account.comparison, plan.sizing]
sources: [investment_assistant/workbench/paper_strategies.py, investment_assistant/workbench/paper_fill.py, docs/specs/2026-10-31-v2.11-accounts-and-paper-trading-design.md]
---

## 是什么

照计划执行账户不让模型再次选择买哪份计划：程序逐项处理所有可买的 Agent 计划，用本账户最大允许股数挂单。它与[Agent 自管](account.agent_portfolio.md)共用计划来源、[成交规则](account.fill_rules.md)和仓位限制，观察的是组合选择与执行行为的差别；这不是规则基线与 Agent 的比较。

## 怎么算 / 从哪来

可买集合取未结束 `agent-3` 链的**最新入场期版本**，有效期须覆盖下一交易日；`next_open` 只在下一交易日恰为首个有效日时可买。链在真实账户中持仓、退出或已平仓，只作为信息，不直接排除模拟账户的买入机会。

- 没有本链持仓或在挂单，且 `max_shares > 0`：按 `max_shares` 挂买单。股数先受风险、单票、总仓位三条上限约束，再扣已有挂单占用，受现金与持仓数量上限约束；无空间记 `no_room` 或 `max_positions`。
- 在挂买单对应的链有新入场期版本：撤旧单，按新版重新检查并挂单；链结束则撤单。已成交持仓仍使用买入时冻结的那一版，不随修订改价位。
- 每个版本至多挂出一次（宿主拒绝的订单不算）；一条链至多成交一次，入场作废或持仓退出后整条链都不能再买。订单过期或被撤只消耗该版本，新修订版仍可成为机会。
- 同一账户同一标的至多一个未平持仓；已经持有该标的时，即使出现新链也不加买，以保留唯一的冻结计划。

账户不主动提前卖出，也不因链被作废而自动清仓；已有持仓只按冻结计划的止损、T1 与最长持有规则退出。

## 在系统里哪里看到

「账户 → 执行记录」看每天的挂单、换单、撤单与跳过原因；「成交」追溯具体计划版本。比较前先看是否因现金或名额不足跳过，不能把没有挂单都理解为策略拒买。
