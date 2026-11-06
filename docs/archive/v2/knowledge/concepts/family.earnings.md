---
id: family.earnings
title: 财报日
group: family
version: 1
applies_to: [fetch-1, plan-2, screen-1]
summary: "记录预计财报日期及取数时点；主源 yfinance，实际启用的备源为 Nasdaq，日期未知不能解释为没有财报。"
related: [plan.earnings, system.adapter_registry, system.backup_source, family.fundamentals]
sources: [investment_assistant/data/adapters/earnings.py, investment_assistant/data/sources/nasdaq.py, investment_assistant/data/repo.py]
---

## 是什么

财报日回答「下一次财报预计何时发布」，不是已经披露的财务数字。它给[计划财报规则](../../../../knowledge/concepts/plan.earnings.md)与每日筛选提供时间边界；实际数字另看[公司基本面](family.fundamentals.md)。

## 怎么算 / 从哪来

- 主源是 yfinance calendar；主源最终失败且类别允许、备源开关开启时，对该标的尝试一次 Nasdaq JSON。
- 写入 `earnings_dates`，保存日期、`source`、`fetched_at`；同答案不追加，不把重复查询伪装成新数据。读取按截止时间选择当时已知行。
- daily 目标与日线的宽集合一致，但去掉 SPY 与行业 ETF；手动、研究与补查使用显式标的。最近 `fetched_at` 早于 session 前7天为滞后。
- 每个 scope 单独记录拉取结果；`source_used` 是来源 id，`fallback` 给出是否尝试及主源原因。

## 在系统里哪里看到

「数据 → 财报日」看覆盖、逐目标结果和来源；计划的财报提示、筛选的近期财报过滤也读取它。

## 注意

预计日期会改动；缺失、过期或取数失败都不是「没有财报」。Nasdaq 备源成功也不代表日期保证准确，相关计划仍为 Gate HOLD。
