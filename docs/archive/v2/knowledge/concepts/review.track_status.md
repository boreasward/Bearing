---
id: review.track_status
title: "三方对比状态"
group: review
version: 1
applies_to: [tracks-1, tracks-2]
summary: "三方对比里每条轨道的结果状态共九种（tracks-2 另加开盘作废、并入其他链），如缺失、计划作废、待定、已平仓；它们决定该轨道是记 0R、记具体 R，还是暂不计入。"
related: [review.tracks, review.r_multiple, review.version_basis, review.plan_shadow]
sources: [investment_assistant/review/tracks.py]
---

## 是什么
三方对比的每个格子都有一个状态，页面上显示固定的中文，代码里是下面的英文标识：

| 状态 | 中文 | 含义 | R |
| --- | --- | --- | --- |
| `missing` | 缺失 | 没有计划或没有运行结果，或 Agent 运行失败 | 空 |
| `not_executable` | 计划作废 | 计划本身按规则作废，无法执行 | 0R |
| `pending` | 待定 | 计划还在有效期内没成交，或成交后还算不出 | 空 |
| `expired` | 到期未成交 | 有效期结束仍没碰到入场区间 | 0R |
| `open` | 未平仓 | 已成交还没卖完 | 浮动 R |
| `closed` | 已平仓 | 已成交且全部卖出 | 实际 R |
| `pass` | 不建议 | Agent 给出的是不建议买 | 0R |
| `not_executed` | 未执行 | 你的决定不是买，或有效期已过仍没买 | 0R |
| `no_plan` | 无计划成交 | 你买了，但决定没有关联计划，没法按计划算 R | 空 |

`tracks-2`（v2.9）另有两种：`voided`（开盘作废）——`plan-shadow-2` 里开盘落到止损以下等情形，本计划作废，记 0R；`merged`（并入其他链）——Agent 轨道的单元被并入同标的另一条活跃链，没有自己的 Agent 轨道，不计入均值、单列计数。

## 怎么算 / 从哪来
规则轨道和 Agent 轨道的状态来自计划影子的结果（`pending`、`expired`、`open`、`closed`）加上"计划作废"和"缺失"两种情形；Agent 轨道还有 `pass`；实际轨道有 `not_executed`、`pending`、`open`、`closed` 和 `no_plan`。汇总时，`pending` 单独计数，`missing` 与 `no_plan` 合并计为"缺失"；均值只用 R 不为空的格子。

## 在系统里哪里看到
「复盘 → 轨道对比」每行的轨道状态；汇总里的 `by_status` 计数。

## 注意
"空"与"0R"的区别很重要：0R 是确实没产生盈亏，空是还没算出来，见 [R](review.r_multiple.md)。
