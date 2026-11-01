---
id: agent.citation
title: "引用"
group: agent
version: 1
applies_to: [agent-2, agent-1, agent-review-1]
summary: "Agent 的每个价位和每条理由都要标明依据：call:N 指本次运行的第 N 次工具调用，user:<id> 指你贴入的某条用户输入；引用不存在整条作废。"
related: [agent.tool_calls, agent.user_input, agent.action_levels, agent.reject_reasons]
sources: [investment_assistant/execution/citations.py, investment_assistant/execution/plan_check.py, investment_assistant/analysis/agent/tools.py]
---

## 是什么
引用是 Agent 说"我凭什么这么说"的凭据。Agent 写的每个价位、每条论点、每条失效条件，都必须带着引用；系统据此能回头查到它当时到底读了什么。

引用有两种写法：

- `call:N`：本次运行里第 N 次工具调用的结果。编号从 1 开始，按调用先后给，出错的调用也占一个编号。
- `user:<id>`：你贴入的某条[用户输入](agent.user_input.md)。只有出现在复查或修订提示词的"用户输入"证据块里的 id 才能被引用。

## 怎么算 / 从哪来
校验分几档，宽严不同：

- **提议里的价位和论点**：引用必须真实存在，否则整份建议作废（`invalid_citation`），不是只丢掉那一条。每个价位还必须至少引用一次价格类工具（`get_price_bars` 或 `get_indicators`），只引用资讯或申报不算数。
- **失效条件**：每条都要带引用，缺引用算条件无效（`invalid_condition`）。
- **相对上版变化**：每条说明都要带引用，引用不存在为 `invalid_citation`。
- **反方的反证与风险**：引用不存在或论点编号不存在的条目被丢弃并计数，不会让整份建议作废。
- **每日复查结论**：结论为修订或作废时必须引用，维持可以不引用；引用不存在为 `invalid_citation`。

`get_concept` 读概念卡的调用同样有编号，同样可以被引用；它不属于价格类工具，所以不能当作价位的依据。

## 在系统里哪里看到
「今日」和计划页的 Agent 计划块里，价位、论点旁的引用编号可以点开，看到当时工具调用的参数、返回值和 SHA-256 前 8 位。`user:<id>` 的引用只显示、不可点开。

## 注意
引用只证明"这句话有来源"，不证明结论正确。一份建议可以引用齐全却仍然判断错误。
