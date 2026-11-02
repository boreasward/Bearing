---
id: agent.portfolio_manager
title: 组合经理意见
group: agent
version: 1
applies_to: [agent-pm-1, portfolio-check-1]
summary: "组合经理只解释硬结果、不改价位股数；功能关闭跳过整个组合节点。开启且硬结果已成功产出后，PM 失败才保留结果并降级。"
related: [account.portfolio_check, account.agent_portfolio, system.feature_flags, system.gate_hold]
sources: [investment_assistant/analysis/agent/pm_check.py, investment_assistant/execution/portfolio.py, investment_assistant/analysis/agent/pm.py, investment_assistant/workflow/nodes/portfolio.py, docs/specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md]
---

## 是什么

组合功能开启时，`agent-pm-1` 在 review 之后、accounts 之前，基于真实账户的[组合硬检查](account.portfolio_check.md)说明取舍。每个模拟账户虽各算硬结果，但不另调一个 PM；Agent 自管决策另用 `agent-portfolio-2`。

## 怎么算 / 从哪来

- 读取宿主冻结的 `fact:portfolio_check`；工具限 `get_concept/get_knowledge/get_indicator/get_market_context`，独立300秒预算，token 计每日总量。
- 输出 summary（至多300字）、ranking（plan_id/rank/reason）、tradeoffs（text/refs）、hold_actions（ticker/suggestion/reason）。suggestion 仅 `keep/trim/exit`。
- ranking 只能是硬判定 ok 的计划，plan_id 与正整数 rank 都不重复；模型可以建议排序，但不会重排宿主现金装入结果。
- 任何层级的价位或股数字段都拒绝；tradeoffs 的引用必须来自本次 call 或 `fact:portfolio_check`，不能用虚构来源绕过硬限制。
- `portfolio_manager=true` 且硬结果已成功产出后，PM 成功才写 commentary；后续模型或意见校验失败则保留硬结果，显示 `degraded`。不能把纯硬检查的成功称成 PM 已完成建议。
- `portfolio_manager=false` 时整个 portfolio 节点 `skipped/feature_disabled`，不是单独停 PM；`[fact:portfolio_check]` 写「本版未检查」，`validate_orders` 收到 `None`，不作组合拦截，Today 为 `not_computed`。

## 在系统里哪里看到

「今日 → 组合检查」分开显示硬顺序与经理建议；开启且已成功产出硬结果、后续 PM 失败时才标「只有硬结果」。账户查询与 Today 使用相同状态封装，功能关闭或缺硬结果为 `not_computed`。

## 注意

意见不是订单，也不是对单条计划的价位修订。只排序 ok 不证明建议有效；Gate HOLD 与用户自己决定、下单的边界保持。
