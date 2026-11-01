---
id: indicator.high_low
title: 区间高低点
group: indicator
version: 1
applies_to: []
summary: "20 日、252 日的最高价与最低价；注意放量突破规则用的是「前 20 日收盘新高」，口径与此不同。"
related: [rule.volume_breakout, indicator.breadth, family.bars]
sources: [investment_assistant/analysis/agent/tools.py, investment_assistant/analysis/rules.py, investment_assistant/analysis/regime.py]
---

## 是什么

区间高低点是一段时间内出现过的最高价和最低价，用来判断现价处在近期区间的什么位置：接近区间顶端说明强势，接近底端说明弱势。252 个交易日大约是一年，所以常叫「52 周」高低点。

## 怎么算 / 从哪来

系统里有三种相近但**不是同一口径**的「新高」，别混着读：

- **工具与关键数据里的区间高低**：`high_20`、`low_20`、`high_252`、`low_252`，取最近 n 根日线（含当天）的最高价的最大值、最低价的最小值；历史不足 n 根时为空。
- **放量突破规则**：收盘（全复权）高于**前 20 个交易日**的最高收盘，不含当天；用收盘价、不用盘中最高价。
- **市场广度的 52 周新高 / 新低**：当天全复权收盘价大于等于（或小于等于）前 251 个收盘价的最大（或最小）值，要求至少 252 根日线。

## 在系统里哪里看到

- Agent 工具 `get_indicators` 返回 20 日与 252 日高低；「标的」页的关键数据。
- [放量突破](rule.volume_breakout.md)候选里冻结的 `prior_high`；[广度](indicator.breadth.md)里的新高新低计数。

## 注意

- 同一天的「区间高点」和「突破用的前高」可能不是同一个数，因为一个用最高价、一个用收盘价。
- 创新高只是价格事实，不等于会继续上涨；相关规则**未经验证**。
