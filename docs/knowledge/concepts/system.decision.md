---
id: system.decision
title: "决定与执行依据"
group: system
version: 2
applies_to: []
summary: "v2.9 起退役：不再有采纳 / 否决 / 降级按钮，采纳由成交推出；旧决定只读保留，旧链、旧复盘卡、旧周报仍按旧决定显示。"
related: [system.decision_unit, review.position, review.version_basis, review.tracks, plan.own, review.execution_status]
sources: [investment_assistant/execution/ledger.py, investment_assistant/execution/trades.py]
---

## 是什么
**v2.9 起退役。** 系统给出候选和计划，决定权在你，但你不再点按钮：录入成交时关联你照着做的计划，系统由成交推出采纳（见[执行状态与备注](review.execution_status.md)）。`POST /api/workbench/decisions` 返回 410 `decisions_retired`，新成交带 `decision_id` 返回 400 `decision_link_retired`。

`signal_decisions` 表与旧行保留**只读**：旧决定在旧口径里照旧生效（链状态 `closed_by_decision`、三方对比的 `decision` 版本依据、旧复盘卡与旧周报的显示），新链不会进入这些状态。下面讲的是旧决定当时怎么记的。

## 怎么算 / 从哪来
- **三种决定**：采纳（最终动作等于建议动作）、否决（最终动作为忽略）、降级为观察（最终动作为观察，建议动作本身是观察时不能再降级）。必须写一句理由。
- **执行依据**：采纳买入时从三者中选一个——Agent 计划（第 n 版）、规则计划、自定价位。Agent 计划可用时默认选 Agent，否则规则计划，再否则自定。选自定价位时，会另存一份自定计划，与决定在同一事务里写入，建议股数按新价位重算（见[自定计划](plan.own.md)）。否决和降级不带计划。
- **记录你看到的 Agent 版本**：提交时附带页面显示的 Agent 版本；如果后台已经修订到更新的一版，决定会被拒绝（"Agent 计划已更新到第 n 版，请刷新后再决定"），避免你基于过期的版本做决定。这条记录就是[决定时看到的版本](review.version_basis.md)。
- **更正**：再提交一条新的决定，指向旧决定；同一请求编号重复提交返回原决定。

## 在系统里哪里看到
只在旧数据里：「标的」页历史里的旧决定、旧复盘卡的「你的决定」、`review-7` 及更早的周报「按决定」。新数据看「今日」计划表的执行状态和复盘的「按执行」。

## 注意
决定只是记录，不会替你下单。成交要你在券商自己下，再到「复盘 → 交易与持仓」录入（见[持仓与成交](review.position.md)）。
