---
id: account.agent_portfolio
title: "Agent 自管账户"
group: account
version: 1
applies_to: [account-1, agent-portfolio-1]
summary: "Agent 在宿主给定的可买计划与股数上限内选择买卖、撤单和继续持有；不能改计划价位或取消自动退出。"
related: [account.paper, account.follow_plans, account.fill_rules, plan.sizing, plan.max_hold]
sources: [investment_assistant/workbench/agent/graph.py, investment_assistant/workbench/agent/portfolio_check.py, investment_assistant/workbench/paper_strategies.py, docs/specs/2026-10-31-v2.11-accounts-and-paper-trading-design.md]
---

## 是什么

Agent 自管账户让 Agent 看本账户的现金、持仓、在挂单、可买计划与市场信息，再决定组合明天怎么行动。宿主先算好事实和限制，`agent-portfolio-1` 只负责在这个范围内作选择。

## 怎么算 / 从哪来

- **能决定**：从 `buyable` 选择计划，买入正整数股且不超过该计划的 `max_shares`；撤掉本账户仍在挂的买单；为已有持仓下全部或一半的卖单，按次一交易日开盘模拟成交；给没有卖单的每只持仓写继续持有的理由。
- **必须说明**：每条订单与每只继续持有各有 1–300 字理由，总结为 1–200 字；未买的可买计划无需逐个解释。超出现金、股数或名额的订单被拒绝，宿主不会悄悄缩小股数；被拒的订单不消耗该计划版本，次日可在证据里看到拒绝原因并改正后重下。
- **不能决定**：不能买计划之外的标的、另定入场价或改冻结计划的止损、T1、最长持有；宿主的止损、T1 卖半和[时间止损](plan.max_hold.md)持续执行，Agent 只能更早卖，见[成交规则](account.fill_rules.md)。
- **能读的工具**：只有 `get_price_bars`、`get_indicators`、`get_market_context`、`get_concept`、`get_knowledge`；标的查询限本账户持仓与可买计划的并集，不额外放行基准股票。行情事实按本次截止时间取数。

每个账户每个实际运行交易日至多一行有效决策。Agent 不可用、超时或被跳过时，已有挂单与自动退出仍照常结算，当天不产生新的 Agent 决策订单；记录写明跳过原因。[照计划执行](account.follow_plans.md)使用同一批 Agent 计划，可用于观察选择与仓位行为的差别。

## 在系统里哪里看到

「账户 → Agent 决策」展开一天，看「Agent 看到的」事实、原始决定、订单理由、继续持有理由、宿主检查与后续成交结果。模型给了订单不等于订单通过，更不等于已成交，要沿订单状态追溯。
