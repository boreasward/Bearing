---
id: agent.action_levels
title: "动作与价位"
group: agent
version: 1
applies_to: [agent-2, agent-1]
summary: "Agent 对买入候选给 buy 或 pass；买入时给入场下沿、上沿、止损、T1 四个价位，每个都要引用价格类工具，并过顺序与 ±50% 校验。"
related: [agent.citation, agent.thesis, agent.reject_reasons, plan.entry_zone, plan.stop, plan.target1]
sources: [investment_assistant/execution/citations.py, investment_assistant/analysis/agent/graph.py]
---

## 是什么
Agent 轨道对每个买入候选独立给出一份建议：动作只有两种，`buy`（建议买）和 `pass`（不建议买）。给 `buy` 时要同时给四个价位：入场下沿 `entry_low`、入场上沿 `entry_high`、止损 `stop`、目标 T1 `target1`。它和规则给出的 [入场区间](plan.entry_zone.md)、[止损](plan.stop.md)、[目标 T1](plan.target1.md) 并排显示，互不覆盖。

## 怎么算 / 从哪来
价位不是公式算出来的，是模型读了工具返回的数据之后自己写的；系统的作用是检查它写得是否站得住。检查按固定顺序进行，第一个不通过的就是作废原因，不做任何补救：

1. 输出必须是合法的 JSON 对象（`malformed`）。
2. 动作只能是 `buy` 或 `pass`，带一句摘要和论点，论点每条都有文字和引用（`malformed`）。
3. 动作为 `buy` 时四个价位都要有数值和引用（`malformed`）。
4. 引用必须是本次运行真实发生过的工具调用（`invalid_citation`）。
5. 每个价位至少引用一次价格类工具，也就是 `get_price_bars` 或 `get_indicators` 的结果（`invalid_citation`）。
6. 价位必须满足 止损 < 入场下沿 ≤ 入场上沿 < T1，并且每个价位都落在生成时最近收盘价的 ±50% 以内（`level_check`）。

价位四舍五入到分。动作为 `pass` 时不给价位，只留摘要和论点。提示词告诉模型：计划在信号日后第 1 到第 3 个交易日有效，到 T1 后卖一半、剩余按 3 倍 ATR 移动止损，不要考虑仓位大小；仓位由系统按入场区间中点另算。

## 在系统里哪里看到
「今日」的 Agent 计划块里，动作与价位一行，每个价位旁都有引用编号，点开能看到当时的工具调用。「展开推导」会核对价位与已存的 Agent 输出一致。

## 注意
价位来自模型输出，不构成投资建议。校验只保证"有来源、顺序对、不离谱"，不保证价位合理；Agent 与规则一样**未经验证**（见 [证据状态「未验证」](system.gate_hold.md)）。被作废的建议只显示原因，不显示内容，原因含义见 [作废与跳过原因](agent.reject_reasons.md)。
