---
id: indicator.breadth
title: 广度
group: indicator
version: 1
applies_to: [regime-2]
summary: "样本池里站上 MA50 / MA200 的股票占比与 52 周新高新低数量；合格样本不足 20 只时为「样本不足」。"
related: [indicator.regime, indicator.moving_average, indicator.high_low]
sources: [investment_assistant/analysis/regime.py, docs/archive/v2/features/pool-and-context.md]
---

## 是什么

广度看的不是某一只股票，而是「一篮子股票里有多少比例处在上升趋势」。大盘指数可能被几只权重股托着上涨，而广度能告诉你上涨是不是普遍的。它是市场区制判断的唯一输入。

## 怎么算 / 从哪来

- **样本**：纳指 100 范围 ∪ 手动观察池中，**当天有日线**的股票。不用单独的观察池，是因为池每天会变，广度会跟着名单波动。范围文件加载失败时退回只用手动观察池，记 `regime_universe_missing`。持仓股不在样本里就不计入。
- 全部按全复权收盘价计算：
  - 站上 MA50 的占比 `pct_50` = 收盘 > MA50 的只数 ÷ 有至少 50 根日线的只数；
  - 站上 MA200 的占比 `pct_200`，要求至少 200 根日线；
  - 52 周新高 / 新低只数：收盘 ≥（或 ≤）前 251 个收盘的最大（或最小）值，要求至少 252 根日线。
- 某只股票历史不够，只在它不够格的那一项里被排除，其他项照常计入；合格样本为空时占比为空。

## 在系统里哪里看到

- 「今日」顶部的市场环境；Agent 工具 `get_market_context` 的 `breadth`。
- 据此得出的[区制](indicator.regime.md)标签。

## 注意

- 有 200 根日线的股票不足 20 只时，区制一直显示「样本不足」。
- 广度是描述性的，未经验证它对交易有帮助（Phase 0 Gate HOLD）。
