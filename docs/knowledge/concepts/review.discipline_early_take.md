---
id: review.discipline_early_take
title: "过早止盈"
group: review
version: 1
applies_to: [review-6, discipline-2]
summary: "持有期内没到 T1 就卖出，而卖出之后 20 个交易日内价格到了 T1，记为过早止盈；20 个交易日走完前显示未成熟。"
related: [review.discipline_chase, review.discipline_held_loser, plan.target1, rule.plan_stop, review.retro_card]
sources: [investment_assistant/review/discipline.py]
---

## 是什么
你在计划目标 T1 之前就卖掉了，结果价格后来还是到了 T1：说明你比计划更早离场，少赚了一段。

## 怎么算 / 从哪来
对每笔买入的每一段平仓，同时满足以下三条才标记 `early_take`：

1. 从买入日到卖出日，价格最高价没有碰到 [T1](plan.target1.md)（买入之前的高点不算）。
2. 这次卖出不是在执行止损：如果卖出发生在计划止损（`plan_stop`）触发后的一个交易日之内，视为按计划执行，不算。
3. 卖出之后的 20 个交易日里，有任何一天最高价达到 T1。

卖出后的 20 个交易日还没走完时，结果显示为"未成熟"，不提前下结论。

## 在系统里哪里看到
「复盘」纪律明细里的"过早止盈"计数与未成熟计数，以及每笔成交的标记。

[复盘卡](review.retro_card.md)上的"你的执行与纪律"也显示这一项：卡页同时给出检测当时（冻结）和现在的判定，待定的项写明还要等什么；周度复盘的"你学到了什么"汇总本周结束的卡上的合计。

## 注意
事后看价格到了 T1 并不意味着当时应该持有；这一项只是让你看到自己离场与计划的差距。
