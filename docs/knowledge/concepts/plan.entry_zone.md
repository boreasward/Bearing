---
id: plan.entry_zone
title: 入场区间
group: plan
version: 1
applies_to: [plan-1]
summary: "参考价上下各 0.5 个 ATR14，价位四舍五入到分；成交价高出上沿会被复盘记为「追高进场」。"
related: [plan.ref_price, indicator.atr14]
sources: [investment_assistant/execution/plan_levels.py, docs/archive/v2/features/daily-plan.md]
---

## 是什么

入场区间是计划认为可以接受的买入价格带：不要求你买在某个精确的点，只要成交落在这条带里就算按计划入场。

## 怎么算 / 从哪来

- 入场下沿 = 参考价 − 0.5 × ATR14；入场上沿 = 参考价 + 0.5 × ATR14。
- 价位四舍五入到分（0.01）。
- 四个价位必须满足 止损 < 入场下沿 ≤ 入场上沿 < 目标 T1，否则这份计划不生成，记跳过原因 `atr_unavailable`。

## 在系统里哪里看到

- 「今日」计划块的「入场区间 x–y」；「标的」页 K 线上的计划价位线。
- 复盘里：成交价高于入场区间上沿，会被记为追高进场；影子模拟如何入场见计划影子。

## 注意

- 区间宽度只取决于波动率，与你的资金、仓位无关。
- 你可以在采纳时改成自己的价位，见[自定计划](plan.own.md)。
- 规则**未经验证**（Phase 0 Gate HOLD）：区间只是算术结果，不是买入建议。
