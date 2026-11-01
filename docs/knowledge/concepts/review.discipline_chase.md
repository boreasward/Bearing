---
id: review.discipline_chase
title: "追高进场"
group: review
version: 1
applies_to: [review-6, discipline-2]
summary: "买入成交价高于所关联计划的入场区间上限，就记为追高进场；在成交当时判定。"
related: [review.discipline_unplanned, review.discipline_early_take, review.discipline_held_loser, plan.entry_zone, review.retro_card]
sources: [investment_assistant/review/discipline.py]
---

## 是什么
纪律指标检查你的执行是否背离了计划。追高进场指的是：你买入的价格已经超出计划愿意付的上限。

## 怎么算 / 从哪来
对每笔未作废的买入成交，找到它关联的计划（`discipline-2` 起是成交时关联的那一版；旧成交走它的旧决定所选的计划），比较成交价与该计划的入场上沿（[入场区间](plan.entry_zone.md)的上限）：成交价更高就标记 `chase`。上沿会换算到买入当日的拆股口径再比较。成交时就能判定，不需要等待。

没有关联计划的买入不判这一项，归入[计划外交易](review.discipline_unplanned.md)。

## 在系统里哪里看到
「复盘」的纪律明细：每笔成交带标记，另有各项计数和遵守率（有计划的买入中没有被标记的比例）。

[复盘卡](review.retro_card.md)上的"你的执行与纪律"也显示这一项：卡页同时给出检测当时（冻结）和现在的判定，待定的项写明还要等什么；周度复盘的"你学到了什么"汇总本周结束的卡上的合计。

## 注意
这是对执行的描述，不是对错的判决：计划被你自定调整过时，以你采纳的那份计划为准。
