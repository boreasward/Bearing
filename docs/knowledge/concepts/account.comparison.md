---
id: account.comparison
title: "账户对比"
group: account
version: 1
applies_to: [account-1, monthly-5]
summary: "账户按同一截止时间与公式并列，曲线以开账当日或之后首个交易日的完整权益归一到 100；起点、资金和现金流不同，不排名。"
related: [account.ledger, account.paper, account.agent_portfolio, account.follow_plans, review.monthly_return, review.r_multiple]
sources: [investment_assistant/workbench/accounts.py, investment_assistant/workbench/account_views.py, investment_assistant/workbench/monthly.py, docs/specs/2026-10-31-v2.11-accounts-and-paper-trading-design.md]
---

## 是什么

账户对比在同一截止时间用同一组公式并列真实账户、模拟账户与 SPY，帮助观察现金使用、计划选择和退出行为。每个账户的开账日期、初始金额与出入金可能不同；归一后的曲线仍可能覆盖不同历史区间，因此不排名、不标最好、不宣称某个策略有效。

开账日 `opened_on` 保留原始记录；API 的 `opening_session` 是开账当日或之后首个交易日，按交易所日历跳过周末和节假日。曲线以**该首个交易日完整且为正的账本权益 = 100**；缺首日权益或该日估值不完整时不绘制该账户曲线，不改用更晚日期作基准。

## 怎么算 / 从哪来

| 指标 | 读法 |
| --- | --- |
| 自开户收益率 | 修正 Dietz：起点 = 开户首个交易日（`opening_session`）的权益 − 当时的累计净出入金，开账带入的持仓算在起点里、不算收益；收益率 =（最新权益 − 起点 − 累计净出入金）÷（起点 + 首日之后各笔出入金 × 区间剩余天数占比），首日及以前的出入金并入起点。区间自开户首个交易日收盘起算；首日缺权益、该日估值不完整或起点不大于 0 时为空 |
| 本月收益率（本月至今） | （当前截止 session 权益 − 上月最后交易日权益 − 区间净出入金）÷ 上月最后交易日权益。两端须为精确日期的完整权益；缺端点或估值不完整时，仅可用同两日的有效券商快照回退，否则为空；起点权益不大于 0 时也为空。SPY 使用相同两日 |
| 最大回撤 | 每日权益相对此前最高权益的最大跌幅：（峰值 − 谷值）÷ 峰值，附峰谷日期；按权益原序列算，出入金也会影响读数 |
| 结束交易数、胜率、平均 R | 连同样本数读；已确定的 R 样本少于 5 时，平均 R 为空并写「样本 < 5」，不能把空值当作 0R |
| 纪律、跳过计划、持仓与挂单 | 看哪些计划因主动选择、资金或名额限制没有执行，再解释收益差异 |

账户页的本月至今收益由读视图独立计算，终点随截止 session 推进，净出入金只计上月最后交易日之后、截止 session 之内的未作废记录。[完整月报的月度收益](review.monthly_return.md)仍按 `monthly-5` 取上月与本月最后交易日，并冻结当期结果；本月至今计算不会改写月报。

[Agent 自管](account.agent_portfolio.md)与[照计划执行](account.follow_plans.md)都买 Agent 计划，差别包含选择、仓位与提前卖出。真实账户还包括人工执行与记录完整性的影响；模拟账户的无滑点等假设也会影响结果。小样本或不同起点不能支持策略优劣结论。

## 在系统里哪里看到

「账户 → 对比」看叠加曲线与指标表，先核对各列开账日、首个交易日、截止时间、口径版本、资金流和缺数据提示。周度复盘、月度复盘与复盘卡仍只处理真实账户；模拟结果在账户页读取，不会冒充你的实际交易复盘。
