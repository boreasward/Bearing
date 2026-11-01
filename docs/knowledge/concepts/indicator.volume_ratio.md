---
id: indicator.volume_ratio
title: 量比与均量
group: indicator
version: 1
applies_to: [rules-1]
summary: "规则用「当日成交量 ≥ 前 50 日均量 × 1.5」判断放量；工具另给 20 日均量和量比，两者口径不同。"
related: [rule.volume_breakout, indicator.high_low, family.bars]
sources: [investment_assistant/analysis/rules.py, investment_assistant/analysis/agent/tools.py]
---

## 是什么

成交量表示当天有多少股被买卖。突破时成交量明显放大，通常被看作更多资金参与、突破更可信；量比就是把当天成交量和平时的量相比。

## 怎么算 / 从哪来

- **规则（放量突破）**：当日成交量 ≥ 前 50 个交易日平均成交量 × 1.5。这 50 日**不含当天**，倍数 1.5 是事先固定的阈值（`volume_multiple`）。候选里冻结 `volume`、`avg_volume_50` 供核对。
- **工具与关键数据**：`avg_volume_20` = 最近 20 根日线（**含当天**）的平均成交量；`volume_ratio` = 当天成交量 ÷ `avg_volume_20`。不足 20 根日线时为空。
- 因为分母的窗口和是否含当天都不同，工具里的量比不能直接拿来对照规则的 1.5 倍。

## 在系统里哪里看到

- [放量突破](rule.volume_breakout.md)的触发条件表；Agent 的 `get_indicators`；「标的」页关键数据。

## 注意

- 成交量单位是股数；除权、拆股前后的成交量不可比，系统以日线里已按拆股调整后的数据为准。
- 放量不代表方向，只是说明当天关注度高；相关规则**未经验证**。
