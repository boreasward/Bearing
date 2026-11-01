---
id: plan.status
title: 计划状态与作废原因
group: plan
version: 1
applies_to: [plan-1]
summary: "计划只有 ok / invalid 两种状态；作废原因（决定性的）与提示（仅提醒的）要分开读。"
related: [plan.earnings, plan.sizing, plan.context_flags, review.track_status]
sources: [investment_assistant/execution/plan_levels.py, docs/features/daily-plan.md]
---

## 是什么

每份规则计划有一个状态：`ok`（可用）或 `invalid`（作废）。让它作废的是「作废原因」；而「提示」只是提醒，不影响状态。

## 怎么算 / 从哪来

| 类别 | 代码 | 含义 |
| --- | --- | --- |
| 作废原因 | `earnings_in_validity` | 有效期内有财报，见[财报规则](plan.earnings.md) |
| 作废原因 | `no_room` | 单票或总持仓上限下没有空间，见[建议仓位](plan.sizing.md) |
| 提示 | `earnings_unknown`、`earnings_within_10d` | 财报日不明或临近 |
| 提示 | `regime_risk_off`、`regime_unavailable`、`sector_weak` | 市场环境，见[市场环境提示](plan.context_flags.md) |

任何一条作废原因成立，状态就是 `invalid`。另外，没有资金快照时仓位状态记 `no_equity`：只给每股风险、不给股数，既不作废也不算提示。ATR 算不出来或价位顺序不对时，根本不生成计划，记跳过原因 `atr_unavailable`。

## 在系统里哪里看到

- 计划块上的「已作废」与原因；推导视图的校验段。
- 作废的计划在复盘的三方对比里记为「计划作废」，见[三方对比状态](review.track_status.md)。

## 注意

- 重跑批处理时，结果相同就返回原计划；结果不同则记入运行报告的冲突列表，**不覆盖**原计划。
- 在[自定计划](plan.own.md)里，规则计划的作废原因降为提示，状态总是 `ok`。
