---
id: indicator.volatility_20
title: 近 20 日波动率
group: indicator
version: 1
applies_to: [rules-1]
summary: "最近 20 个日收益的标准差（不含当天）；当日涨跌幅绝对值达到它的 2.5 倍，就触发「异常波动」。"
related: [rule.abnormal_move, indicator.gap, indicator.atr14]
sources: [investment_assistant/analysis/rules.py]
---

## 是什么

波动率衡量一只股票的日收益平时「摇摆」多大。系统用它当尺子，判断当天的涨跌是否大到不寻常——同样涨 4%，对平时每天动 0.5% 的股票是大事，对平时每天动 3% 的股票只是日常。

## 怎么算 / 从哪来

- 用全复权收盘价算最近 21 个日收益（每个 = 当天 ÷ 前一天 − 1）；其中**前 20 个**（不含当天）的样本标准差，就是 `stdev_20`。
- 这是日波动率，没有年化。
- [异常波动](rule.abnormal_move.md)的条件：当日收益不为 0，且其绝对值 ≥ 2.5 × `stdev_20`（阈值 `sigma` = 2.5）。

## 在系统里哪里看到

- 异常波动候选里冻结的 `return` 与 `stdev_20`，在「今日」的触发依据里逐条核对。

## 注意

- 它和 [ATR14](indicator.atr14.md) 都描述波动，但口径不同：ATR 用美元的真实波幅，这里用收益率的标准差。
- 当天出现新财报时不触发异常波动，改由「新财报」规则观察；规则**未经验证**。
