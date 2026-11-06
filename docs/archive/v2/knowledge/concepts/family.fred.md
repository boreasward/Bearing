---
id: family.fred
title: FRED 利差
group: family
version: 1
applies_to: [regime-2]
summary: "来自 FRED 的两条宏观序列 BAMLH0A0HYM2 与 T10Y2Y，各取 120 天；只作背景，未配置时如实记录。"
related: [indicator.hy_oas, indicator.t10y2y, indicator.regime, system.append_only]
sources: [investment_assistant/data/adapters/fred.py, investment_assistant/analysis/regime.py, investment_assistant/data/families.py, docs/archive/v2/features/pool-and-context.md]
---

## 是什么

FRED 是美联储圣路易斯分行的公开经济数据库。系统从中取两条和市场风险偏好相关的序列：高收益债信用利差（[HY 信用利差](../../../../knowledge/concepts/indicator.hy_oas.md)，序列号 `BAMLH0A0HYM2`）和 10 年期减 2 年期美债利差（[2s10s 利差](../../../../knowledge/concepts/indicator.t10y2y.md)，序列号 `T10Y2Y`）。它们是市场环境快照的宏观部分。

## 怎么算 / 从哪来

- 每次批处理在采集阶段取这两条序列，各取最近 120 天，附「20 个观测前」的变化。
- 观测值只追加：同一天的数值被修订，就追加一行，读取时按截止时间还原当时的值。
- 需要环境变量 `FRED_API_KEY`。没有配置时不发请求、什么都不存，运行报告里记 `not_configured`，页面如实显示「未配置」而不是空白。
- 「覆盖」的口径：有观测值的序列数；最新观测早于最近交易日前 7 个自然日的列为滞后——这两个口径互相独立，一条序列可以既覆盖又滞后。

## 在系统里哪里看到

- 「数据」菜单的「FRED 利差」卡，浏览区可切换两条序列，显示实际值与修订次数。
- 「今日」顶部的市场环境；Agent 的 `get_market_context` 工具返回里也有。

## 注意

- 利差**只作背景信息**，不参与区制标签的判定（标签只看[广度](../../../../knowledge/concepts/indicator.breadth.md)），也不改计划价位。
- 密钥只从环境变量读取，不会出现在日志和页面上。
