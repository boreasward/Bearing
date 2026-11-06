---
id: system.discussion
title: "就此计划讨论"
group: system
version: 1
applies_to: [agent-discuss-1]
summary: "在计划卡或复盘卡上向 Agent 追问：它只能读，用十三个只读工具回答，只写你的提问、回答、运行记录和可选的经验草稿；截止时间是你提问的那一刻。"
related: [system.my_knowledge, agent.tool_calls, agent.knowledge_citation, system.cutoff, agent.user_input, agent.citation, review.retro_card, plan.derivation]
sources: [investment_assistant/review/discuss_tools.py, investment_assistant/review/agent_runs.py, investment_assistant/analysis/agent/graph.py]
---

## 是什么
讨论是对一份计划或一张复盘卡的追问："这个止损怎么来的""当时为什么没引用那条消息"。回答由 Agent 给出，每个回答带引用、可以看它读了什么；它不改计划，也不改任何记录。

## 怎么算 / 从哪来
- **入口**：计划卡（"今日"与计划页）、计划页页头、"标的"页的计划行用计划上下文；复盘卡页用卡上下文。
- **只读**：模型没有任何写工具。它能用批处理的十个工具（含 `get_knowledge`），再加三个讨论专用的只读工具：`get_plan_context`（讨论对象的上下文）、`get_derivation`（某个计划的推导）、`get_agent_run`（某次 Agent 运行的推理步骤，工具结果每条截断到 2000 字）。专用工具只回答本次讨论的单元内的计划与运行，越界返回"只能查本次讨论的计划与运行"；批处理的提议、反方、复查、解读和复盘卡起草都看不到它们。
- **卡上下文不含草稿**：复盘卡的上下文只有冻结的事实、是否已审定、审定记录和已采纳的知识；起草出的叙述与教训草稿不给讨论读，复盘卡的起草运行也不能用 `get_agent_run` 读。
- **截止时间**：提问的那一刻。工具读到的是截至提问时刻的数据；上下文同时写明"计划当时的截止时间"，回答要区分"当时看到的"与"之后发生的"。
- **回答要带引用**：只能引用本次工具返回过的 `call:N`、`concept:`、`know:`，上下文里出现过的 `user:<id>`，或上下文本身 `fact:context`；不满足则这一轮标"未通过引用校验"，原文折叠。
- **只写四类行**：你的一轮、回答、这次回答的运行与工具调用，以及可选的一条经验草稿。（每次 Agent 运行还会让 LangGraph 的检查点表（PostgresSaver）多几行，那是框架的运行记录，不在这四类里；核对「只写四类行」时把检查点表排除在外。）草稿只有回答通过校验、且写明适用范围与反例时才写；不合格只丢弃草稿、回答照常。草稿要你审定通过才成为[我的知识](system.my_knowledge.md)的新条目或新版本。
- **补充信息**：把你的提问"存为补充信息"走的是[用户输入](agent.user_input.md)，作用于计划链；讨论本身不触发复查，要进计划得点"重新复查"。

## 在系统里哪里看到
计划页与复盘卡页列出该上下文已有的讨论；讨论页按时间列出每一轮，回答下可展开引用、看这次回答读了什么，带经验草稿的有"去审定"。

## 注意
讨论里的模型和批处理里是同一个模型，回答同样未经验证，引用只保证"指向的东西存在"，不保证结论正确。服务重启会中断正在回答的那一轮，页面标"中断"，可以再问。
