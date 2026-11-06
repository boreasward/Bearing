---
id: indicator.relative_strength
title: 相对 SPY 强弱
group: indicator
version: 1
applies_to: [condition-1]
summary: "rs20 / rs60 = 标的区间收益减 SPY 同期收益（全复权收盘价）；为正表示跑赢大盘，Agent 的 rs_below 条件用它。"
related: [indicator.screen_rs, indicator.sector_strength]
sources: []
---

## 是什么

相对强弱回答「这只股票比大盘强还是弱」。系统用 SPY（标普 500 ETF）当大盘的代表：同一段时间里标的涨得比 SPY 多，相对强弱就为正。

## 怎么算 / 从哪来

- rsN = 标的 N 日收益 − SPY 同期 N 日收益，N 取 20 和 60；收益 = 最新全复权收盘价 ÷ N 个交易日前的全复权收盘价 − 1。
- 标的与 SPY 都至少要有 N + 1 根日线，否则为空，显示「不可用」。
- 结果是小数：`0.05` 表示比 SPY 多涨 5 个百分点。

## 在系统里哪里看到

- Agent 工具 `get_indicators` 返回 `rs20`、`rs60`；「标的」页关键数据。
- Agent 的失效 / 复查条件 `rs_below`：窗口取 20 或 60，数值由 Agent 给出；当 rsN 小于该数值时条件成立，rsN 不可用则为「无法判定」，见失效条件。

## 注意

- 它只是价格表现的比较，不说明原因；SPY 本身下跌时，「比 SPY 强」仍可能是亏钱的。
- 每日筛选排序用的是另一种相对强弱，基准是 QQQ 而不是 SPY，见[筛选相对强弱](indicator.screen_rs.md)。
