---
id: indicator.screen_rs
title: 筛选相对强弱
group: indicator
version: 1
applies_to: [screen-1]
summary: "RS = ½(r20 − r20(QQQ)) + ½(r60 − r60(QQQ))；每日筛选用它给触发组、形态组排序，基准是 QQQ。"
related: [system.screen, indicator.relative_strength, indicator.dollar_volume_20]
sources: [investment_assistant/analysis/screen_rules.py, docs/features/pool-and-context.md]
---

## 是什么

每日筛选在纳指 100 里每天最多新选 20 只，需要一个排序依据。它用「最近 20 日和 60 日，各比 QQQ 强多少」的平均来排：越强的越靠前。

## 怎么算 / 从哪来

- 对每只通过过滤的股票：r20 = 全复权收盘价的 20 日收益，r60 同理；对基准 QQQ 也算一遍。
- RS = ½ × (r20 − r20(QQQ)) + ½ × (r60 − r60(QQQ))。20 日和 60 日权重各一半。
- 排序：先排「触发组」（当天 `trend_reclaim` 或 `volume_breakout` 触发买入且没有卖出候选），再排「形态组」（收盘 > MA50 > MA200 且 ATR14 ÷ 收盘在 2%–6%）；每组内按 RS 从高到低，同分按代码。每天最多入选 20 只，其余记 `rank_cut`。

## 在系统里哪里看到

- 「今日」筛选卡片的形态候选（默认前 10 只，可展开全部）和每只的 `rs`、`rank`。

## 注意

- 基准是 QQQ，不是[相对 SPY 强弱](indicator.relative_strength.md)里用的 SPY；两个数不能互相比较。
- 排序只决定谁先入选，不代表后市表现；筛选规则**未经验证**。
