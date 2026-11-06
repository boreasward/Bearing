---
id: indicator.sector_strength
title: 行业强弱
group: indicator
version: 1
applies_to: [regime-2]
summary: "SMH / XLK / QQQ 相对 SPY 的 20 日、60 日强弱，归为强、弱、分化、数据不足四种状态。"
related: [indicator.relative_strength, plan.context_flags, indicator.regime]
sources: [investment_assistant/analysis/regime.py, investment_assistant/analysis/pool.py, docs/archive/v2/features/pool-and-context.md]
---

## 是什么

行业强弱看一只股票所在的板块是顺风还是逆风。系统只维护三个行业 ETF：SMH（半导体）、XLK（科技）、QQQ（纳指 100），每只股票通过观察名单里的 `sector:<ETF>` 标签映射到其中一个。

## 怎么算 / 从哪来

- 对每个 ETF 算 `rs20`、`rs60`：ETF 的 20 日（60 日）全复权收益减去 SPY 同期收益，口径同[相对 SPY 强弱](indicator.relative_strength.md)。
- 状态：
  - 两者都为负 → 弱（`weak`）；
  - 两者都为正 → 强（`strong`）；
  - 其余 → 分化（`mixed`）；
  - ETF 或 SPY 任一不足 61 根日线 → 数据不足。

## 在系统里哪里看到

- 「今日」顶部的市场环境；Agent 的 `get_market_context` 里的 `sectors`。
- 行业为弱时，计划加提示 `sector_weak`，见[市场环境提示](plan.context_flags.md)；Agent 的条件 `sector_weak` 在所属行业转弱时成立，行业未映射或状态不可用时为「无法判定」。

## 注意

- 没有设置行业标签的股票没有行业强弱。
- 与区制一样，它只加提示，不改价位；也**未经验证**。
