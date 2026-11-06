---
id: indicator.dollar_volume_20
title: 20 日平均成交额
group: indicator
version: 1
applies_to: [screen-1]
summary: "最近 20 日 收盘价 × 成交量 的平均值；每日筛选要求不低于 5000 万美元，否则记为流动性不足。"
related: [indicator.screen_rs]
sources: [investment_assistant/analysis/screen_rules.py, docs/archive/v2/features/pool-and-context.md]
---

## 是什么

成交额是一天里成交的钱数，比成交量更能说明一只股票是不是「好进好出」。每日筛选不想把流动性差的股票推到你面前——这类股票挂单容易吃不到、滑点大。

## 怎么算 / 从哪来

- 20 日平均成交额 = 最近 20 根日线各自的（收盘价 × 成交量）之和 ÷ 20，价格用拆股调整的收盘价，单位美元。
- 门槛为 5000 万美元（`MIN_DOLLAR_VOLUME` = 50,000,000）；低于门槛的股票在筛选里标 `illiquid` 并停止后续过滤，不进入触发组或形态组。

## 在系统里哪里看到

- 「今日」的筛选卡片计数里的「流动性不足」；筛选结果里每只股票的 `dollar_volume_20`。
- 过滤顺序见每日筛选。

## 注意

- 门槛是固定的，没有用历史数据调参。
- 它只衡量过去 20 天的活跃度，不保证你下单时的成交质量。
