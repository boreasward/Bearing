---
id: review.discipline_unplanned
title: "计划外交易"
group: review
version: 1
applies_to: [review-6, discipline-2]
summary: "买入成交没有关联任何计划，或成交日在所关联版本的有效期之外，记为计划外交易；它不进入遵守率的分母。"
related: [review.discipline_chase, review.position, system.decision, review.retro_card]
sources: [investment_assistant/review/discipline.py, investment_assistant/execution/ledger.py]
---

## 是什么
系统的闭环是"信号、计划、成交"。计划外交易指你的买入没有走完这条链：要么没有关联任何计划，要么成交日已超出所关联那一版的有效期。

## 怎么算 / 从哪来
`discipline-2`（v2.9）对每笔未作废的买入成交，看它的 `plan_id` 关联的计划（规则基线、Agent 版本或自定计划都算，见[执行状态与备注](review.execution_status.md)）：没有关联计划、或成交日在关联版本的有效期外（`out_of_validity`），就标记 `unplanned`。v2.9 之前的旧成交没有 `plan_id`，再看它的旧[决定](system.decision.md)带没带计划，口径同 `discipline-1`。

这类成交无法计算追高、过早止盈、死扛亏损，所以遵守率只对**有计划的买入**计算：没有被任何项标记的有计划买入数 ÷ 有计划买入数，计划外交易不进分母。

## 在系统里哪里看到
「复盘」纪律明细的"计划外交易"计数；周度复盘（`review-8` 起）单独列出"计划外成交"（之前叫"无信号交易"）。录入成交时选择关联的计划，可以避免被记为计划外。

[复盘卡](review.retro_card.md)上的"你的执行与纪律"也显示这一项：卡页同时给出检测当时（冻结）和现在的判定，待定的项写明还要等什么；周度复盘的"你学到了什么"汇总本周结束的卡上的合计。

## 注意
计划外并不一定是坏事（也许是你自己的研究），只是系统无法用计划对它复盘。
