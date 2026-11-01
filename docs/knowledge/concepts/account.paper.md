---
id: account.paper
title: "模拟账户"
group: account
version: 1
applies_to: [account-1, paper-fill-1, stages-5, daily-2]
summary: "模拟账户用日线在纸上结算 Agent 计划，分别观察 Agent 自管与照计划执行；默认无费用、无滑点，不连接券商。"
related: [account.ledger, account.agent_portfolio, account.follow_plans, account.fill_rules, account.comparison]
sources: [investment_assistant/workbench/accounts.py, investment_assistant/workbench/paper_fill.py, investment_assistant/workbench/paper_strategies.py, docs/specs/2026-10-31-v2.11-accounts-and-paper-trading-design.md]
---

## 是什么

模拟账户是用独立现金和成交记录观察交易计划执行结果的纸上账户。可建[Agent 自管](account.agent_portfolio.md)与[照计划执行](account.follow_plans.md)两种策略，默认初始现金为 100,000 美元；它们都从已有 Agent 计划中买入，不自行扩展到计划之外的股票。

## 怎么算 / 从哪来

每日复查后、推送前的 `accounts` 节点先结算此前的挂单与退出，再记每日[账本权益](account.ledger.md)，最后为本次交易日产生下一交易日的订单。漏跑的交易日按日线逐日补结算，只在本次实际运行的交易日作新决策；当天刚下的单当天不成交。

成交使用开、高、低、收四个日线价格，按共享的[成交规则](account.fill_rules.md)推演；默认每笔费用为 0，可在账户参数中调整，没有滑点与部分成交假设。两种策略都受现金、风险、单票、总仓位和持仓数量上限约束，默认最多 8 个持仓与在挂买单名额。

日线看不出盘中路径、排队与真实成交量，模拟结果因此有不确定性；这些记录不是真实成交，也不证明规则或 Agent 有效。程序不连接券商，不能替你完成真实交易。

## 在系统里哪里看到

「账户」切换到模拟账户后，看独立的权益、持仓、成交、在挂单和每日决策；[对比](account.comparison.md)按同一口径并列展示。读成交时同时看 `paper-fill-1`、使用的日线日期与缺数据提示。
