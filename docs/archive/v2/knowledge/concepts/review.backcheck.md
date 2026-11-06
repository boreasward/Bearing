---
id: review.backcheck
title: "上线前检查"
group: review
version: 1
applies_to: [backcheck-2]
summary: "新规则上线前手动运行，用库内已有数据回看触发频率、止损是否总被扫掉、信号后超额等，只是描述性检查，不代表规则有效。"
related: [review.plan_shadow, review.r_multiple, system.gate_hold, rule.trend_reclaim]
sources: [investment_assistant/optimize/backcheck.py]
---

## 是什么
上线前检查是对一条规则的体检：不联网、不补采，只读库里已有的日线和 SEC 申报，回头看这条规则如果一直在跑，会触发多频繁、止损会不会总被扫掉。

## 怎么算 / 从哪来
- 每只股票的前 200 根日线只用来预热指标；评估区内逐日只用截至当日的日线跑规则（假设无持仓）。买入候选按 `plan-1` 算价位，再用[计划影子](review.plan_shadow.md)模拟结果。
- **按规则统计**：触发数、每 100 个股票日的触发数、同一天至少 3 只股票同时触发的天数、成交数与过期数、5 个交易日内止损的占比、到达 T1 的占比、R 的均值、中位数与四分位。
- **信号后超额**：每条信号、每个 h ∈ {5, 10, 20} 个交易日，按收盘价对收盘价计算标的相对 SPY 的超额；第 h 日在最后一根日线之后记未成熟（`immature`），标的或 SPY 缺任一端点记缺失（`missing`），都不进统计、不用邻近值替代。
- **标记**：`too_frequent`（每 100 个股票日触发超过 5 次）、`stops_swept`（成交至少 10 笔且 5 日内止损占比至少 50%）、`insufficient`（成交少于 10 笔）。

结果冻结进记录；`backcheck-1` 的旧记录保留，超额列显示"—"。

## 在系统里哪里看到
「复盘 → 上线前检查」；也可用命令行 `python -m investment_assistant.optimize.backcheck` 运行。

## 注意
这只是描述性检查，不调参，也不代表规则有效；超额是信号日收盘到第 h 日收盘，不等于可成交。当前观察池成交样本太少，常被标为 `insufficient`，属于正常现象。
