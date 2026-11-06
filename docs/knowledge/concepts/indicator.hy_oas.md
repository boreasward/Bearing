---
id: indicator.hy_oas
title: HY 信用利差
group: indicator
version: 1
applies_to: [regime-2]
summary: "FRED 序列 BAMLH0A0HYM2，高收益债相对国债的期权调整利差；只作背景信息，附 20 个观测前的变化。"
related: [indicator.t10y2y, indicator.regime]
sources: [investment_assistant/data/adapters/fred.py, investment_assistant/derivation/indicators/breadth.py]
---

## 是什么

高收益债（俗称垃圾债）比国债多付的利息，叫信用利差。市场担心违约、风险偏好下降时，投资者要求的补偿变多，利差就会走阔；风险偏好好时利差收窄。`BAMLH0A0HYM2` 是 ICE BofA 美国高收益债指数的期权调整利差，单位是百分点。

## 怎么算 / 从哪来

- 来自FRED，每次批处理取最近 120 天。
- 展示最新值，以及与 20 个观测之前相比的变化（`change`）及对应日期；观测不足 21 个时没有变化值。
- 同一天被修订的值追加记录，读取时按截止时间还原。

## 在系统里哪里看到

- 「今日」顶部的市场环境、「数据」菜单的 FRED 浏览，以及 Agent 的 `get_market_context` 里的 `macro`。

## 注意

- 它是背景信息，**不参与区制标签**，不改计划价位。
- 没有配置 FRED 密钥时显示「未配置」；单位和变化都是描述性数字，不是交易信号。
