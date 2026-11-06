---
id: account.agent_portfolio
title: "Agent 自管账户"
group: account
version: 2
applies_to: [account-1, agent-portfolio-2, agent-portfolio-1]
summary: "agent-portfolio-2 在组合功能开启时读取本账户硬结果并拦 blocked 买单；功能关闭则标本版未检查，不作组合拦截，原价位与自动退出保持。"
related: [account.portfolio_check, agent.portfolio_manager, account.paper, account.follow_plans, account.fill_rules, plan.sizing, plan.max_hold]
sources: [investment_assistant/execution/portfolio.py, investment_assistant/execution/portfolio_check.py, investment_assistant/execution/paper/strategies.py, docs/specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md]
---

## 是什么

Agent 自管账户让 Agent 看本账户的现金、持仓、在挂单、可买计划与市场信息，再决定组合明天怎么行动。`agent-portfolio-2` 在旧版证据外增加 `[fact:portfolio_check]`：组合功能开启时读取本账户同 session 的硬结果，关闭时写「本版未检查」；它不是给真实账户写建议的 `agent-pm-1`。

## 怎么算 / 从哪来

- **能决定**：从 `buyable` 选择计划，买入正整数股且不超过该计划的 `max_shares`；撤掉本账户仍在挂的买单；为已有持仓下全部或一半的卖单，按次一交易日开盘模拟成交；给没有卖单的每只持仓写继续持有的理由。
- **必须说明**：每条订单与每只继续持有各有 1–300 字理由，总结为 1–200 字；未买的可买计划无需逐个解释。超出现金、股数或名额的订单被拒绝，宿主不会悄悄缩小股数；被拒的订单不消耗该计划版本，次日可在证据里看到拒绝原因并改正后重下。
- **不能决定**：不能买计划之外的标的、另定入场价或改冻结计划的止损、T1、最长持有；宿主的止损、T1 卖半和[时间止损](../../../../knowledge/concepts/plan.max_hold.md)持续执行，Agent 只能更早卖，见[成交规则](account.fill_rules.md)。
- **能读的工具**：`get_price_bars`、`get_indicators`、`get_market_context`、`get_concept`、`get_knowledge`，新版增加 `get_indicator`；标的查询限本账户持仓与可买计划的并集，不额外放行基准股票。行情事实按本次截止时间取数。
- **组合拦截**：本账户 blocked 买单取首硬理由拒绝。新增拒绝码为 `sector_cap`、`max_per_sector`、`single_cap`、`total_cap`、`held_ticker`；single_cap 包含单笔风险与单票敞口。deferred 仍可提交、继续过原现金校验；卖单与撤单不受组合 gate 影响。未归类只是提示。
- **缺行与开关**：`portfolio_manager=false` 时整个 portfolio 节点 `skipped/feature_disabled`，证据段写「本版未检查」，`validate_orders` 收到 `None`，不作组合拦截，Today 为 `not_computed`；原现金、股数等校验仍执行。真正缺组合行时 detail 记 missing，不伪造通过结果。只有 `portfolio_manager=true`、硬结果已成功产出且后续 PM 失败时，才保留硬结果与相应约束、显示 `degraded`。照计划执行账户不读取新 gate，`follow-plans-1` 不变。

每个账户每个实际运行交易日至多一行有效决策。Agent 不可用、超时或被跳过时，已有挂单与自动退出仍照常结算，当天不产生新的 Agent 决策订单；记录写明跳过原因。[照计划执行](account.follow_plans.md)使用同一批 Agent 计划，可用于观察选择与仓位行为的差别。

## 在系统里哪里看到

「账户 → Agent 决策」展开一天，看「Agent 看到的」事实、原始决定、订单理由、继续持有理由、宿主检查与后续成交结果。模型给了订单不等于订单通过，更不等于已成交，要沿订单状态追溯。

## 注意

决策保存实际 agent_version，权益/对比页标版本、参数和开关分界；旧 agent-portfolio-1 的证据与数值保留。新增硬检查不使前向实验获得有效性证明，仍为 Gate HOLD。
