---
id: agent.tool_calls
title: "工具调用"
group: agent
version: 1
applies_to: [agent-2, agent-1, agent-review-1, agent-brief-1, agent-retro-1, agent-discuss-1]
summary: "Agent 只能通过十个只读工具取数：八个读市场与你的数据、get_knowledge 读你审定过的经验（都受截止时间约束），get_concept 读概念卡（唯一不受截止时间约束的例外）；讨论里另有三个专用的只读工具。"
related: [agent.citation, system.cutoff, system.concept_card, agent.reject_reasons, agent.knowledge_citation, system.discussion, system.my_knowledge]
sources: [investment_assistant/analysis/agent/tools.py, investment_assistant/analysis/agent/graph.py, investment_assistant/core/knowledge.py]
---

## 是什么
Agent 不能自己上网，也不能直接读数据库；它想知道什么，只能调用下面十个工具（[就此计划讨论](system.discussion.md)里另有三个专用的，见表后）。工具是普通的只读函数，由系统在每个单元开始前把标的、交易日和截止时间装进去，模型改不了。

| 工具 | 用途 |
| --- | --- |
| `get_trigger` | 本单元触发了哪些规则、各自的输入值（不含规则计划的价位，Agent 看不到） |
| `get_price_bars` | 日线开高低收和成交量，默认 60 根，最多 120 根 |
| `get_indicators` | 收盘价、ATR14、MA20/50/200、20 日与 252 日高低点、均量与量比、相对 SPY 的 rs20 / rs60 |
| `get_filings` | 近若干天的 SEC 申报元数据，默认 30 天，最多 90 天 |
| `get_news` | 近若干天的资讯标题与链接，默认 7 天，最多 14 天 |
| `get_earnings` | 下次财报日期与状态 |
| `get_market_context` | 本交易日的区制标签、广度、行业强弱与利差 |
| `get_position` | 你当前的持仓股数与成本，无持仓为空 |
| `get_concept` | 读概念卡：不给 id 返回全部 id 与标题，给 id 返回整张卡 |
| `get_knowledge` | 读你审定过的经验（[我的知识](system.my_knowledge.md)）：给标的或概念 id 取挂在上面的条目，不给取索引；只返回截止时间之前写入的版本，引用写 `know:<id>@<版本>`（见[引用知识](agent.knowledge_citation.md)） |

讨论专用的三个只读工具，批处理的各阶段（提议、反方、复查、解读、复盘卡起草）都看不到：

| 工具 | 用途 |
| --- | --- |
| `get_plan_context` | 本次讨论对象的上下文：计划（价位、论点、条件、版本、复查、决定与成交、影子结果）或复盘卡（冻结事实、是否已审定、采纳的知识；不含教训草稿） |
| `get_derivation` | 本单元内某个计划的推导，只接受本单元的计划 id |
| `get_agent_run` | 本单元内某次 Agent 运行的推理步骤，工具结果每条截断到 2000 字；不含讨论自己的运行 |

## 怎么算 / 从哪来
- **不接受日期参数**：模型没法指定"看哪一天"。取数的截止时间由批处理传入，价格、申报、资讯、财报日和市场环境都只读截止时间之前已入库的数据（见 [截止时间](system.cutoff.md)）。`get_trigger` 读本单元当日的触发候选，`get_position` 读你的持仓记录。
- **标的限制**：带 `ticker` 参数的工具只能查本单元的标的和 SPY、SMH、XLK、QQQ，其他标的返回错误。
- **轮数上限**：每个阶段最多 8 轮带工具调用的模型回复，超过则提议或复查作废（`tool_rounds_exceeded`）。
- **每次调用留痕**：参数、返回值和 SHA-256 都写进 `agent_tool_calls`，出错的调用也记成一个错误信封并占一个编号，供[引用](agent.citation.md)。
- **`get_knowledge` 按截止时间读**：只返回截止时间之前写入的版本，之后的新版本、之后的归档都看不到；结果写明 `point_in_time: true`；它读的是我的知识的条目、版本和状态，不读任何草稿。迁移 027 未执行时返回错误信封"我的知识不可用：迁移 027 未执行"，只影响这一个工具。
- **`get_concept` 是唯一的例外**：概念卡随代码版本走，任何时候读到的都是当前版本的卡，不按截止时间回读；结果里写明 `point_in_time: false`，并带 `concept_ref` 和 `content_sha256`，原样记入调用记录，推理阅读视图也会对这类调用标出"不受截止时间约束"。它不读数据库，卡里只有定义，不含价格、标的或日期数据，详见 [概念卡](system.concept_card.md)。

## 在系统里哪里看到
Agent 计划块里的引用编号展开后就是这些调用；推理阅读视图按时间顺序列出每次调用的参数和返回值。

## 注意
`get_knowledge` 返回的是你的判断，不是事实，价位不能只引用它。截止时间约束的是工具能读到的数据，不保证模型的判断可靠；`get_concept` 的例外也不改变这一点，它只给 Agent 解释系统术语。
