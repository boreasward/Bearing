---
id: agent.version_chain
title: "版本链"
group: agent
version: 1
applies_to: [agent-2, timeline-1]
summary: "同一单元（标的加信号日）的全部 agent-2 版本组成一条链：第 0 版来自信号日批处理，之后只由复查生成，每版有自己的截止时间、有效期和影子。"
related: [review.chain_state, agent.review_outcome, agent.changes, agent.legacy, system.cutoff]
sources: [investment_assistant/execution/timeline.py, investment_assistant/execution/chains.py]
---

## 是什么
Agent 的计划不是一次写完就不管：每天收盘后系统会复查它，必要时修订。一个单元（某只标的的某个信号日）下所有 `agent-2` 版本按先后连成一条版本链。

## 怎么算 / 从哪来
- **第 0 版**（`origin = signal`）：信号日当天批处理的 `agent` 阶段生成，通过校验才成为第 0 版。
- **第 1 版起**：只由复查产生，来源是每日批处理的 `review`（`origin = review`）或你手动点"重新复查"（`origin = recheck`）。每一版用 `supersedes_plan_id` 指向上一版，链不分叉。
- **每版独立**：各有自己的截止时间（生成时刻，见[截止时间](system.cutoff.md)）、`as_of_session`（生成时的交易日）、与上一版的宿主差异和模型说明（见[相对上版变化](agent.changes.md)），也各自做影子跟踪；旧版写入后不再改动。
- **有效期**：第 0 版是信号日后第 1 到第 3 个交易日；修订版从复查日次一交易日起，到复查日后第 3 个交易日。入场期（还没成交）的链有上限：任何一版的有效期都不晚于信号日后第 10 个交易日；持仓期没有这个上限。
- **链的状态**（入场期、持仓期、已作废等）见[版本链状态](review.chain_state.md)，只有入场期和持仓期的链会被复查。

## 在系统里哪里看到
计划页 `#plan/<id>` 的"版本链"：v0 到 vn 每版一行（来源、生成与截止时间、价位、有效期、差异、影子状态）；打开旧版时页头提示最新版是第几版。「今日」的 Agent 计划块标"第 n 版"。

## 注意
迁移 026 之前生成的 `agent-1` 计划不在链里，见 [agent-1 旧版](agent.legacy.md)。修订不是改写历史：旧版始终保留，三方对比取的是你决定时看到的那一版（见[决定时看到的版本](review.version_basis.md)）。

这条口径同样**未经验证**（Phase 0 Gate HOLD，见[证据状态「未验证」](system.gate_hold.md)）：它只说明系统怎么运作，不说明修订有效。
