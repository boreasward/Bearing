---
id: review.version_basis
title: "决定时看到的版本"
group: review
version: 1
applies_to: [tracks-1, tracks-2]
summary: "三方对比的 Agent 轨道取你照着做时看到的那一版：旧决定记了版本取 decision，v2.9 起取第一笔关联买入所关联的版本（fill），都没有取首版（first），被并入其他链的单元记 merged；不拿事后修订美化结果。"
related: [review.tracks, agent.version_chain, system.decision, review.first_latest]
sources: [investment_assistant/review/tracks.py, investment_assistant/review/weekly.py]
---

## 是什么
一个 Agent 计划链可能有好几个版本。比较"Agent 的表现"时必须说清楚取哪一版，否则事后被修订得更好的版本会让结果看起来比当时的真实判断更漂亮。三方对比的做法是：取你**照着做时看到的那一版**。

## 怎么算 / 从哪来
`version_basis` 有四个取值（`tracks-2`，v2.9）：

- `decision`：旧决定里记了 `agent_plan_id`（决定时页面显示的 Agent 版本），就取这一版（旧数据）。
- `fill`：该单元链上第一笔关联买入成交所关联的版本——成交时关联的就是你照着做的那一版（见[执行状态与备注](review.execution_status.md)）。
- `first`：以上都没有时取首版（v2.5 之前的旧决定没有记录时，取该单元的 `agent-1` 计划，没有再取首版）。
- `merged`：这个单元被并入了同标的另一条活跃链（见[一只股票一条活跃链](system.active_chain.md)），没有自己的 Agent 轨道；它不进均值，单独计数。

行上还带 `version_no`（`agent-1` 计划没有版本号，为空）和 `version_basis`（没有 Agent 计划时为空）。`tracks-1` 的旧行只有 `decision` / `first` 两种依据，旧快照照旧显示；`agent-1` 单元的其余字段与 v2.5 之前完全相同。

## 在系统里哪里看到
「复盘 → 轨道对比」每行的 Agent 轨道标明取的是第几版、依据是什么；v2.9 之前提交决定时系统会把页面上显示的版本记进决定（见[决定与执行依据](system.decision.md)）。

## 注意
这样取意味着：你成交（或旧决定）之后才出现的修订版，不会计入这次决定对应的 Agent 轨道结果；它们在"最新版"汇总里另算（见[首版与最新版汇总](review.first_latest.md)）。
