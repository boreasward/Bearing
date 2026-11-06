---
id: account.portfolio_check
title: 组合硬检查
group: account
version: 1
applies_to: [portfolio-check-1, agent-portfolio-2]
summary: "组合功能开启时宿主按账户计算硬结果，未知行业只提示；关闭则整个节点跳过。仅开启且已有硬结果时，后续 PM 失败才保留结果并降级。"
related: [agent.portfolio_manager, account.agent_portfolio, account.follow_plans, family.sector, system.feature_flags, plan.sizing]
sources: [investment_assistant/execution/portfolio.py, investment_assistant/execution/portfolio_types.py, investment_assistant/workflow/nodes/portfolio.py, investment_assistant/api/routes/accounts.py, docs/specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md]
---

## 是什么

`portfolio-check-1` 是不读库、不调模型的确定性检查；组合功能开启时，宿主对真实账户及每个活跃模拟账户独立提供账本、有效参数、截止前计划与行业数据。模型的[组合经理意见](agent.portfolio_manager.md)是另一层，不改变硬结果。

## 怎么算 / 从哪来

- 只对入场期链头给 verdict/order/conflicts；持仓链不再作为新买入候选，只由本账户正数量持仓计入市值与名额。
- 股数复用既有 `max_shares`，按本账户风险、单票/总敞口、现金和名额重算，不信任计划带来的旧股数。
- 行业比例 = (持仓市值 + 有效在挂买单敞口 + 候选敞口) / 权益；`sector_cap` 默认0.35、范围0.05–1.0。每组不同 ticker 数的 `max_per_sector` 默认3、整数范围1–10。严格超过才拦；已有/在挂/重复标的不重复计候选敞口。
- 按 `chain_signal_date` 升序、ticker 次序贪心装入完整候选股数，不够现金标 `deferred/deferred_by_cash`、order为空，不在途中偷偷缩股。
- 判定 `ok` 可执行、`blocked` 被拦截、`deferred` 暂缓。硬理由为 `sector_cap`、`max_per_sector`、`single_cap`、`total_cap`、`insufficient_cash`、`held_ticker`、`max_positions`。其中 single_cap 是「超过单笔风险或单票敞口上限」。
- 未归类单列，`sector_unknown` 只提示，不受两个行业上限约束；仍须满足其它硬限制。权益非正时比例为空，不除零。输出为 exposure_by_sector、verdicts、conflicts、order、params_used、version。
- `portfolio_manager=false` 时整个 portfolio 节点 `skipped/feature_disabled`，不运行硬检查；`[fact:portfolio_check]` 写「本版未检查」，`validate_orders` 收到 `None`，不作组合拦截，Today 为 `not_computed`。既有现金等校验照常，不代表全部订单通过。
- `portfolio_manager=true` 且硬结果已成功产出后，PM 模型失败才保留硬结果、显示 `degraded`。有本账户硬结果时，Agent 自管账户 blocked 买单取首硬理由拒绝，deferred 仍过原现金检查；真实缺行记 missing，不伪造全通过。照计划执行账户不接这个 gate。

## 在系统里哪里看到

今日与账户复用 `{status,reason,session,cutoff_at,result,commentary}`：功能关闭或缺行 `not_computed`；开启且已有硬结果、后续 PM 失败为 `degraded`；完整 `ready`。运行详情按账户列计数；表内顺序与行业敞口取实际硬结果，不能把关闭显示为「只有硬结果」。

## 注意

可执行只表示本次输入符合限制，不保证盈利。未知行业、缺数据与缺行都要可见；计算与所有计划继续 Gate HOLD。
