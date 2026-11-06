---
id: review.r_multiple
title: "R"
group: review
version: 1
applies_to: [plan-shadow-1, tracks-1]
summary: "以计划的止损距离为一个单位（1R）衡量盈亏：赚了多少倍止损距离；0R 表示没交易，空值表示还没算出来，两者不能混。"
related: [review.plan_shadow, review.tracks, review.track_status, plan.stop]
sources: [investment_assistant/execution/simulation.py, investment_assistant/review/tracks.py, investment_assistant/review/discipline.py]
---

## 是什么
R 是复盘里统一的度量单位：把"入场价到止损价的距离"当作 1R，一笔交易的结果就是它赚（或亏）了几倍这个距离。这样不同价位、不同波动的股票可以放在一起比较。大致地说，规则计划的 T1 离参考价是止损距离的两倍，所以叫 2R（以参考价算；实际成交价不同则略有出入）。

## 怎么算 / 从哪来
- **影子计划**：每一段出场的 R = 出场比例 × (出场价 − 入场价) ÷ (入场价 − 初始止损)，各段相加；还没平仓的部分按最新收盘价浮动计算。
- **实际成交**：把平仓段按数量加权，R = (出场价 − 入场成本) ÷ (入场成本 − 买入时的计划止损)；入场成本含买入费用。跨拆股时止损会按拆股比例换算后再比较。
- **止损距离为零**的那一段不参与计算，避免除零。

**0R 与"没算出来"的区别**：下面这些结果记 0R，因为确实没有产生盈亏——计划作废（`not_executable`）、到期未成交（`expired`）、Agent 不建议（`pass`）、你没有执行（`not_executed`）；而待定（`pending`）、缺失（`missing`）和无计划成交（`no_plan`）是"还没算出来或算不出来"，R 为空。汇总的均值和中位数只用已确定的值，不会把空值当 0 拉低或抬高。

## 在系统里哪里看到
复盘里的计划影子、三方对比、纪律明细，月度复盘的敏感性表（见[风险敏感性表](review.sensitivity.md)）。

## 注意
R 只描述结果，不评价决策的好坏；样本很小，均值没有统计意义。各状态的含义见[三方对比状态](review.track_status.md)。
