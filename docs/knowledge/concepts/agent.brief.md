---
id: agent.brief
title: "解读"
group: agent
version: 1
applies_to: [agent-brief-1]
summary: "卖出、观察和冲突单元由 Agent 单独写一份只解读不给动作的摘要，带支持、反证和矛盾处；失败时退回规则版摘要。"
related: [agent.counter, agent.citation, agent.tool_calls, system.decision_unit, system.conflict]
sources: [investment_assistant/analysis/briefs.py, investment_assistant/analysis/agent/graph.py, investment_assistant/execution/citations.py]
---

## 是什么
不是每个单元都需要 Agent 给出买卖建议。对卖出、观察和有冲突的单元，Agent 只做一件事：写一份解读，告诉你这个信号意味着什么、有哪些证据支持、有哪些反证、哪里互相矛盾或信息不足。解读**不含动作、不含价位**，不是交易指令。

## 怎么算 / 从哪来
- **谁跑解读**：无冲突的买入单元不另跑解读，直接取提议的摘要和第一条反证；其余单元（卖出、观察、冲突）各跑一次单阶段解读 `agent-brief-1`，同样可以调用[工具](agent.tool_calls.md)。
- **输出**：一句摘要，加三组条目：支持（`support`）、反证（`contra`）、矛盾或信息不足（`conflicts`），每条都带[引用](agent.citation.md)。资讯只有标题和链接，未核验原文。
- **校验**：格式不对为 `malformed`，引用不存在为 `invalid_citation`，都会让解读作废，页面只显示原因。输出里多出的动作、价位一律丢掉。
- **失败时的退路**：模型不可用、出错或校验失败时，退回规则版摘要，形如"规则候选：……；建议动作……。LLM 未参与"，并标明是回退。

## 在系统里哪里看到
「今日」决策单元卡里的解读（支持 / 反证 / 冲突）；有 Agent 计划的买入单元只显示"解读见 Agent 建议"，不重复。

## 注意
解读是对规则信号的补充说明，不改变规则给出的动作（见[决策单元](system.decision_unit.md)与[冲突与回避](system.conflict.md)）。它和 Agent 计划一样未经验证。

这条口径同样**未经验证**（Phase 0 Gate HOLD，见[证据状态「未验证」](system.gate_hold.md)）：它只说明系统怎么运作，不说明解读有效。
