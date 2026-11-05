---
id: rule.position_drawdown
title: 持仓回撤
group: rule
version: 1
applies_to: [rules-1]
summary: "卖出候选，仅限没有计划的持仓：收盘较成本跌 ≥ 8%，或较持仓以来最高收盘回撤 ≥ 12%。"
related: [rule.plan_stop, plan.stop, review.position, system.decision_unit]
sources: [investment_assistant/analysis/rules.py, investment_assistant/execution/exits.py, docs/archive/v2/features/daily-plan.md]
---

## 是什么

对于没有事先定好止损的持仓，这条规则提供一道兜底：亏得太多或从高点回吐太多，就提醒你卖出。动作是**卖出**。

## 怎么算 / 从哪来

- 只对**没有计划的持仓**生效：持仓的当前未平仓批次里，没有哪一笔买入的决定关联了计划。有计划的持仓改用[计划止损](rule.plan_stop.md)和[计划目标](rule.plan_target.md)。
- 满足任一条即触发（价格为拆股调整的收盘价）：
  - 收盘较持仓平均成本下跌 ≥ 8%（`cost_stop` = 0.08）；
  - 收盘较**开仓以来最高收盘**回撤 ≥ 12%（`trailing_stop` = 0.12）。
- 候选里冻结 `close`、`avg_cost`、`high_since_opened`、`from_cost`、`from_high` 和命中了哪一条（`triggered`）。

## 在系统里哪里看到

- 「今日」持仓对应的决策单元里的候选规则。

## 注意

- 持仓成本与开仓日来自你录入的成交，见[持仓与成交](review.position.md)；成交录错会让它判断错。
- 阈值固定、没有调参；规则**未经验证**（Phase 0 Gate HOLD）。
