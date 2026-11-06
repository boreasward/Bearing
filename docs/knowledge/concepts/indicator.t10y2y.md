---
id: indicator.t10y2y
title: 2s10s 利差
group: indicator
version: 1
applies_to: [regime-2]
summary: "FRED 序列 T10Y2Y，10 年期与 2 年期美债收益率之差；只作背景信息，附 20 个观测前的变化。"
related: [indicator.hy_oas, indicator.regime]
sources: [investment_assistant/data/adapters/fred.py, investment_assistant/derivation/indicators/breadth.py]
---

## 是什么

2s10s 利差是 10 年期美债收益率减 2 年期美债收益率，单位是百分点。通常长债收益率高于短债；当它变成负数（收益率曲线「倒挂」）时，常被当作市场对经济前景谨慎的背景信号。

## 怎么算 / 从哪来

- 来自FRED的序列 `T10Y2Y`，每次批处理取最近 120 天。
- 展示最新值，以及与 20 个观测之前相比的变化；观测不足 21 个时没有变化值。
- 同一天被修订的值追加记录。

## 在系统里哪里看到

- 「今日」顶部的市场环境、「数据」菜单的 FRED 浏览，以及 Agent 的 `get_market_context` 里的 `macro`。

## 注意

- 它是背景信息，**不参与区制标签**，也不改任何计划价位。
- 倒挂与衰退之间的关系是宏观上的经验观察，本系统没有验证它对个股交易有用。
