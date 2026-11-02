---
id: agent.action_levels
title: "动作与价位"
group: agent
version: 2
applies_to: [agent-4, agent-3, agent-2, agent-1]
summary: "提议者在首版公司研究之后给动作与价位；agent-4 是运行版本，计划 schema 仍为 agent-3，新增研究证据不改变价位校验与执行口径。"
related: [agent.company_researcher, agent.research_dossier, agent.citation, agent.thesis, agent.reject_reasons, plan.entry_zone, plan.stop, plan.target1]
sources: [investment_assistant/execution/citations.py, investment_assistant/analysis/agent/research_check.py, docs/specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md]
---

## 是什么
Agent 轨道对每个买入候选独立给出一份建议：动作只有两种，`buy`（建议买）和 `pass`（不建议买）。给 `buy` 时要同时给四个价位：入场下沿 `entry_low`、入场上沿 `entry_high`、止损 `stop`、目标 T1 `target1`。它和规则给出的 [入场区间](plan.entry_zone.md)、[止损](plan.stop.md)、[目标 T1](plan.target1.md) 并排显示，互不覆盖。

本卡也是提议者的说明：`agent-4` 首版先读[公司研究员](agent.company_researcher.md)的事实，再提议与接受反方检查；计划结构仍为 `agent-3`，已有价位、入场类型、最长持有与链口径不因运行版本升级改变。手动研究的 buy/pass/wait 只写档案，不自动成为计划。

## 怎么算 / 从哪来
价位不是公式算出来的，是模型读了工具返回的数据之后自己写的；系统的作用是检查它写得是否站得住。检查按固定顺序进行，第一个不通过的就是作废原因，不做任何补救：

1. 输出必须是合法的 JSON 对象（`malformed`）。
2. 动作只能是 `buy` 或 `pass`，带一句摘要和论点，论点每条都有文字和引用（`malformed`）。
3. 动作为 `buy` 时四个价位都要有数值和引用（`malformed`）。
4. 引用必须是本次运行真实发生过的工具调用（`invalid_citation`）。
5. 每个价位至少引用一次价格类工具，也就是 `get_price_bars` 或 `get_indicators` 的结果（`invalid_citation`）。
6. 价位必须满足 止损 < 入场下沿 ≤ 入场上沿 < T1，并且每个价位都落在生成时最近收盘价的 ±50% 以内（`level_check`）。

价位四舍五入到分。动作为 `pass` 时不给价位，只留摘要和论点。提示词告诉模型：计划在信号日后第 1 到第 3 个交易日有效，到 T1 后卖一半、剩余按 3 倍 ATR 移动止损，不要考虑仓位大小；仓位由系统按入场区间中点另算。

`agent-4` 首版新增研究证据要求：研究为 ok 时，至少一条 thesis 引用 research 阶段的真实 `call:N`；研究 rejected/error/disabled 则豁免并明确写缺研究原因。研究与提议共享 Recorder，保留原调用编号。复查修订、再触发与补查重跑只带入最近研究段（carried），不重跑研究员，也不重新施加首版引用规则；新增单指标/补查工具不允许模型移动本轮截止时间。

## 在系统里哪里看到
「今日」的 Agent 计划块里，动作与价位一行，每个价位旁都有引用编号，点开能看到当时的工具调用。「展开推导」会核对价位与已存的 Agent 输出一致。

## 注意
价位来自模型输出，不构成投资建议。校验只保证"有来源、顺序对、不离谱"，不保证价位合理；Agent 与规则一样**未经验证**（见 [证据状态「未验证」](system.gate_hold.md)）。被作废的建议只显示原因，不显示内容，原因含义见 [作废与跳过原因](agent.reject_reasons.md)。
