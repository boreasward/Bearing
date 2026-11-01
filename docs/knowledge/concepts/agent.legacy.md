---
id: agent.legacy
title: "agent-1 旧版"
group: agent
version: 1
applies_to: [agent-1]
summary: "v2.5 之前生成的 agent-1 计划原样只读保留，仍做影子跟踪和三方对比，但没有失效条件、复查条件和版本链，不参与每日复查。"
related: [agent.version_chain, agent.action_levels, agent.counter, review.chain_state]
sources: [investment_assistant/execution/timeline.py, investment_assistant/execution/plan_levels.py, investment_assistant/analysis/agent/graph.py]
---

## 是什么
v2.5 引入版本链和每日复查之前，Agent 轨道生成的计划版本叫 `agent-1`。这些计划不迁移、不回填，保持写入时的样子，页面上标"agent-1（旧版，不参与复查）"。

## 怎么算 / 从哪来
- **结构**：只有动作、四个价位、摘要、论点和反方风险（见[动作与价位](agent.action_levels.md)与[关键反证](agent.counter.md)）。没有失效条件、复查条件，也没有"相对上版变化"。
- **不复查**：它不属于任何[版本链](agent.version_chain.md)，链状态记为 `legacy`，不进入每日复查，也不会产生新版。
- **仍然有效的部分**：照常展示，照常做计划影子跟踪，三方对比里取它自己作为该单元的 Agent 计划；汇总"首版 / 最新版"时，每份 `agent-1` 计划算自己的首版，也算自己的最新版。
- 推送里它的 Agent 行保持原样，末尾标"agent-1（旧版，不参与复查）"。

## 在系统里哪里看到
「今日」里标了旧版的 Agent 块（规则基线不折叠）；计划页与「标的」页的计划列表。

## 注意
旧版的结构化信息比 `agent-2` 少，所以不能与新版逐项对比；和新版一样未经验证。

这条口径同样**未经验证**（Phase 0 Gate HOLD，见[证据状态「未验证」](system.gate_hold.md)）：它只说明系统怎么运作，不说明旧版计划有效。
