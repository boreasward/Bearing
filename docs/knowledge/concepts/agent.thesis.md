---
id: agent.thesis
title: "论点"
group: agent
version: 1
applies_to: [agent-2]
summary: "Agent 建议背后的理由，1 到 3 条，每条带引用，系统按顺序编号为 T1 到 T3；条数不对整版作废。"
related: [agent.counter, agent.citation, agent.action_levels, agent.reject_reasons]
sources: [investment_assistant/execution/plan_check.py, investment_assistant/execution/citations.py]
---

## 是什么
论点回答"为什么建议买（或为什么不买）"。每条论点是一句话加一组引用。一份 `agent-2` 建议必须有 1 到 3 条论点，系统按出现顺序给它们编号 T1、T2、T3，后面的[关键反证](agent.counter.md)、复查和修订都用这个编号指代。

## 怎么算 / 从哪来
论点由模型写出，系统只做检查：

- 条数必须在 1 到 3 之间，否则整版作废（`thesis_count`）。
- 每条必须有文字和至少一个引用，引用必须是本次运行真实发生过的工具调用或证据块里的 `user:<id>`（`malformed` 或 `invalid_citation`）。
- 动作为 `pass`（不建议买）时同样要有论点，只检查论点这一项；失效条件、复查条件、变化说明都不要求。

## 在系统里哪里看到
「今日」和计划页 Agent 计划块里的"论点 T1–T3"，每条带引用编号；每条论点下面紧跟反方给出的关键反证。

## 注意
论点是模型的解释，不是事实核查结果；它们的价值在于让你看到推理路径并能追溯到工具数据。修订版会重写论点，变化出现在[相对上版变化](agent.changes.md)里。

这条口径同样**未经验证**（Phase 0 Gate HOLD，见[证据状态「未验证」](system.gate_hold.md)）：它只说明系统怎么运作，不说明论点有效。
