---
id: plan.sizing
title: 建议仓位
group: plan
version: 1
applies_to: [plan-1]
summary: "股数取 min(资金 × risk_pct ÷ 每股风险, 单票上限, 总持仓剩余空间)；缺资金快照时标 no_equity，没有空间则作废为 no_room。"
related: [plan.stop, plan.ref_price, plan.status]
sources: [investment_assistant/execution/plan_levels.py, investment_assistant/config.py, docs/archive/v2/features/daily-plan.md]
---

## 是什么

建议仓位回答「这笔交易买多少股」。思路是先定好单笔最多愿意亏多少（占总资金的比例），再用止损距离反推能买几股，同时不让单只股票或总持仓超过上限。

## 怎么算 / 从哪来

先取你记录的最新资金快照作为总资金 `equity`，再算三个上限，取最小的一个（向下取整到整股）：

| 上限 | 代码 | 算法 |
| --- | --- | --- |
| 风险 | `risk` | 资金 × `risk_pct` ÷ 每股风险 |
| 单票 | `single` | （资金 × `single_cap` − 该股现有市值）÷ 入场上沿 |
| 总持仓 | `total` | （资金 × `total_cap` − 总持仓市值）÷ 入场上沿 |

- 每股风险 = 参考价 − 止损（规则计划为 2 个 ATR）。
- `risk_pct`、`single_cap`、`total_cap` 来自配置，默认分别为 1%、25%、100%。
- 结果状态：最小的是风险上限为 `sized`，是其余两项为 `capped`；没有资金快照时只给每股风险、不给股数，标 `no_equity`。

## 在系统里哪里看到

- 「今日」计划块的「建议 N 股」；推导视图里逐项显示三个上限和采用了哪一个。

## 注意

- 最小值不大于 0，说明没有空间：计划作废，原因 `no_room`；代码保存的是三个上限中的最小值本身，所以股数字段可能是 0，也可能是负数，都表示没有可买的空间。
- 自定计划和 Agent 计划的每股风险按**入场区间中点**减止损来算。
- 这是风险预算的算术结果，不是投资建议，也**未经验证**（Phase 0 Gate HOLD）。
