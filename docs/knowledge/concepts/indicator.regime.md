---
id: indicator.regime
title: 区制
group: indicator
version: 1
applies_to: [regime-2, condition-1]
summary: "按广度把市场分为进攻 / 中性 / 防守 / 样本不足；只给计划加提示，不改价位、仓位和有效性。"
related: [indicator.breadth, plan.context_flags, agent.invalidation, agent.review_when, family.fred]
sources: [investment_assistant/analysis/regime.py, investment_assistant/execution/plan_levels.py, docs/archive/v2/features/pool-and-context.md]
---

## 是什么

区制是对「现在市场整体偏强还是偏弱」的一个粗略标签，每个交易日一个。它的目的只有一个：让你在看一份买入计划时，同时知道大环境是顺风还是逆风。

## 怎么算 / 从哪来

标签只由[广度](indicator.breadth.md)决定，判定顺序固定如下（`pct_200`、`pct_50` 分别是站上 MA200、MA50 的占比）：

| 标签 | 代码 | 条件 |
| --- | --- | --- |
| 样本不足 | `insufficient` | 有 200 根日线的合格样本不足 20 只 |
| 进攻 | `risk_on` | `pct_200` ≥ 60% 且 `pct_50` ≥ 50% |
| 防守 | `risk_off` | `pct_200` < 40% 且 `pct_50` < 30% |
| 中性 | `neutral` | 其余情况 |

阈值是事先固定的，没有用历史数据调参。旧快照保留 `regime-1` 的口径。

## 在系统里哪里看到

- 「今日」顶部的市场环境；Agent 的 `get_market_context`。
- 每份计划冻结的 `inputs.context`；防守区制给计划加提示 `regime_risk_off`，见[市场环境提示](plan.context_flags.md)。
- Agent 的条件 `regime_is`：区制变成指定标签时成立；区制不可用或样本不足时为「无法判定」。

## 注意

- 区制**只加提示，不改任何价位、仓位和计划有效性**。
- 利差（[FRED](family.fred.md)）只作背景展示，不参与标签。
- 这个标签本身**未经验证**：没有证据表明「防守」时少买会更好。
