---
id: indicator.moving_average
title: 均线 MA20 / MA50 / MA200
group: indicator
version: 2
applies_to: [indicator-1, rules-1, screen-1, regime-2]
summary: "20/50/200根的简单均线分别登记 close 与 adj_close 两套：Agent/图表用拆股调整收盘价，规则/筛选/广度用全复权价，本版不统一数值。"
related: [indicator.registry, rule.trend_reclaim, rule.trend_break, rule.volume_breakout, indicator.breadth]
sources: [investment_assistant/derivation/indicators/registry.py, investment_assistant/derivation/indicators/_price_math.py, investment_assistant/derivation/indicators/screen_metrics.py, investment_assistant/derivation/indicators/breadth.py]
---

## 是什么

均线是最近若干个交易日收盘价的平均值，用来抹平日常噪声、看趋势方向：20 日偏短期，50 日偏中期，200 日偏长期。价格在均线上方通常被当作上升趋势，下方当作走弱，系统的趋势类规则就建立在这个朴素的想法上。

## 怎么算 / 从哪来

- MAn = 最近 n 个收盘价的简单平均，n 取 20、50、200；历史不足 n 根时不算。
- **规则 v1、筛选和市场广度用全复权收盘价 `adj_close`**（含分红调整），避免除息日出现假的「跌破」。注册项为 `ma20_adj_close`、`ma50_adj_close`、`ma200_adj_close`。
- **「标的」页 K 线和 Agent 的 `get_indicators` 用拆股调整的收盘价 `close`**。没有分红时两者相同，有分红时数值会略有差异——所以图上均线与规则候选里冻结的 `ma50` 可能差几分钱，以规则候选里冻结的取值为准。
- close 注册项为 `ma20_close`、`ma50_close`、`ma200_close`；两套窗口均为20/50/200根，不设混淆口径的 `ma20` 注册别名。旧 Agent dict 的 ma20/ma50/ma200 仍映射 close 版，原数值/类型不变。
- `indicator-1` 只包装原公式，没有统一两种价格口径；这种统一要另行改口径与验证，不能在注册搬家时悄悄完成。

## 在系统里哪里看到

- 规则里的用法：[趋势回踩修复](rule.trend_reclaim.md)（收盘 > MA50 > MA200，并重新站上 MA20）、[放量突破](rule.volume_breakout.md)（收盘 > MA50）、[跌破 MA50](rule.trend_break.md)。
- 每日筛选的形态组要求收盘 > MA50 > MA200；[广度](indicator.breadth.md)统计站上 MA50 / MA200 的股票占比。
- 「标的」页 K 线图叠加 MA20 / MA50 / MA200。

## 注意

- 均线是滞后指标：它描述已发生的价格，不预测方向。
- 这些规则的阈值是事先固定的，没有用历史数据调参，也**未经验证**（Phase 0 Gate HOLD）。
