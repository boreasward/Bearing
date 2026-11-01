---
id: review.discipline_held_loser
title: "死扛亏损"
group: review
version: 1
applies_to: [review-6, discipline-2]
summary: "持仓触及计划止损后，下一交易日仍未卖出，记为死扛亏损；还没到判定期限时显示未成熟。"
related: [review.discipline_early_take, review.discipline_chase, rule.plan_stop, plan.stop, review.retro_card]
sources: [investment_assistant/review/discipline.py, investment_assistant/execution/exits.py]
---

## 是什么
计划已经告诉你"到这里就走"，你却没走。死扛亏损衡量的就是：止损被触及之后，你拖着没卖。

## 怎么算 / 从哪来
当某个交易日触发了[计划止损](rule.plan_stop.md)（当日最低价触及当前止损）而你仍持有对应批次，系统给一个期限：触发日起的下一个交易日。期限内（含触发日）如果没有任何一笔卖出，就标记 `held_loser`。

- 买入日之前发生的止损不算；批次在止损触发前就已全部卖出的不算。
- 期限还没到时显示"未成熟"，不提前标记。
- 止损价是计划的[止损](plan.stop.md)，触及 T1 后会随[移动止损](plan.trailing_stop.md)上移。

## 在系统里哪里看到
「复盘」纪律明细的"死扛亏损"计数、未成熟计数与每笔成交的标记；「今日」的持仓行会在触发当天给出卖出候选。

[复盘卡](review.retro_card.md)上的"你的执行与纪律"也显示这一项：卡页同时给出检测当时（冻结）和现在的判定，待定的项写明还要等什么；周度复盘的"你学到了什么"汇总本周结束的卡上的合计。

## 注意
该指标只看"卖没卖"，不看卖在什么价位；它不代表止损设置得合理。
