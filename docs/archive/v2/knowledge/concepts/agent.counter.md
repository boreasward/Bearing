---
id: agent.counter
title: "关键反证"
group: agent
version: 1
applies_to: [agent-2, agent-1]
summary: "反方是另一轮模型调用，逐条对论点找反证并列出其他风险；丢弃无效条目并计数，没有反证的论点会被标出，反方不能改价位和动作。"
related: [agent.thesis, agent.citation, agent.brief, agent.action_levels]
sources: [investment_assistant/execution/plan_check.py, investment_assistant/execution/citations.py, investment_assistant/analysis/agent/graph.py]
---

## 是什么
提议通过校验后，系统再让模型扮演反方：它读到这份建议和它的引用，可以继续调用工具补充数据，任务是找出反对这笔交易的证据，并核对建议里的引用和数字是否和工具结果一致。结果分两部分：

- **关键反证**：逐条对应[论点](agent.thesis.md)，每条形如"针对 T1，反证是……"，带引用。
- **其他风险**：与具体论点无关的风险，同样带引用。

## 怎么算 / 从哪来
- 反方**不能改价位和动作**：输出里出现价位或动作，代码层面直接忽略。
- 引用不存在，或指向的论点编号不存在的条目被丢弃，丢弃数量记为 `dropped`。
- 没有任何反证的论点记入 `uncountered`，页面上显示"反方未对此论点给出反证"，不会假装已经被反驳。
- 反方输出格式无效时整段记为无效，所有论点都算未被反驳。
- 反方轮数超过工具上限时，提议保留、反方部分标记为未完成。
- `agent-1` 旧版的反方只给风险，没有按论点对应的反证。

## 在系统里哪里看到
Agent 计划块里每条论点下面的反证，以及"其他风险"一栏；推送里的 Agent 行带"反证：首条反证"。

## 注意
反方是同一类模型扮演的，不是独立的第二意见，也可能漏掉真正的风险。它的作用是把可质疑之处摆出来，而不是替你判断。

这条口径同样**未经验证**（Phase 0 Gate HOLD，见[证据状态「未验证」](system.gate_hold.md)）：它只说明系统怎么运作，不说明反证有效。
