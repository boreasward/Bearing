---
id: review.tracks
title: "三方对比"
group: review
version: 1
applies_to: [tracks-1, tracks-2]
summary: "对同一批买入单元，把规则计划、Agent 计划和你的实际执行三条轨道并列，R 都按各自计划的止损算；均值只用已确定的值。v2.9 起 Agent 轨道取成交关联的那一版，实际轨道取成交关联的计划。"
related: [review.track_status, review.version_basis, review.r_multiple, review.plan_shadow, review.first_latest]
sources: [investment_assistant/review/tracks.py, investment_assistant/review/discipline.py]
---

## 是什么
三方对比把同一个单元在三种做法下的结果放在一起：**规则**（规则基线 `plan-2` / `plan-1` 的影子结果）、**Agent**（Agent 计划的影子结果）、**实际**（你真正的决定和成交）。它回答"如果当初照规则、照 Agent 或照我自己，结果各是多少 R"。

## 怎么算 / 从哪来
- **单元集合**：窗口内有规则计划或有 Agent 提议运行的买入单元。
- **规则轨道**：规则计划的影子结果；计划作废记 0R。
- **Agent 轨道**：该单元选中的那一版 Agent 计划的影子结果，选哪一版见[决定时看到的版本](review.version_basis.md)；Agent 说不建议买（`pass`）记 0R，运行失败记缺失；被并入其他链的单元记 `merged`，不计入均值、单列计数（`tracks-2`）。
- **实际轨道**：你的成交，R 按成交所关联计划的止损算（`tracks-2`；旧成交走旧决定）；没有关联成交且有效期已过记为未执行（0R），还有效记为待定，有买入但没有关联计划记为 `no_plan`。同时记录你照的是规则、Agent 还是自定计划。
- **影子口径**：带执行字段的计划按 `plan-shadow-2`（开盘作废记 `voided`，0R），旧计划按 `plan-shadow-1`，见[计划影子](review.plan_shadow.md)。
- **汇总**：按全部、核心池、扩展池分别统计单元数、已确定数、待定数、缺失数，以及均值、中位数、合计和盈利个数。「没算出来」（待定、缺失、无计划成交）和 0R 分开，均值和中位数只用已确定的值。各状态含义见[三方对比状态](review.track_status.md)。

## 在系统里哪里看到
「复盘 → 轨道对比」，每一行的计划、运行、决定、成交 id 都能追溯到对应接口；周度和月度复盘各冻结一份。

## 注意
样本小、信号同日重叠，只给计数、均值、中位数和分布，不宣称任何一方更好。
