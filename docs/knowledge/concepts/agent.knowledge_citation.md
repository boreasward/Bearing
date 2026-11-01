---
id: agent.knowledge_citation
title: "引用知识"
group: agent
version: 1
applies_to: [agent-2, agent-review-1, my-knowledge-1]
summary: "Agent 可以用 get_knowledge 读你审定过的经验，并在论点、失效条件、反证与复查结论里用 know:<id>@<版本> 引用；只能引用本次返回过的版本，不能单独支撑价位，计划上会记下引用了哪一版。"
related: [system.my_knowledge, agent.citation, agent.tool_calls, system.cutoff, agent.thesis, agent.counter, agent.review_outcome, review.knowledge_performance]
sources: [investment_assistant/analysis/agent/tools.py, investment_assistant/core/knowledge.py, investment_assistant/analysis/agent/runner.py, investment_assistant/analysis/repo.py]
---

## 是什么
[我的知识](system.my_knowledge.md)是你审定过的经验。生成计划、反方审查和每日复查时，Agent 可以先读相关的经验，再把它当作论据之一引用。经验是你的判断，不是行情数据，所以引用它有额外的规矩。

## 怎么算 / 从哪来
- **怎么读**：工具 `get_knowledge` 给一个标的或一个概念 id，返回挂在上面的条目（至多 10 条，超出的记在 `omitted`）；两个都不给返回索引，只有标题和挂靠，不含正文（至多 50 条）。两个不能同时给，标的范围同其它工具（本单元标的与 SPY、SMH、XLK、QQQ）。
- **怎么引用**：写 `know:<id>@<版本>`，版本必须写。可以出现在论点、失效条件、变化说明、反证、风险和复查结论的引用里。
- **只能引用本次返回过的**：校验由程序做——引用没出现在本次 `get_knowledge` 的返回里（没读过、读的是别的条目或别的版本），整份计划或复查作废，原因是 `invalid_citation`。
- **不能单独支撑价位**：每个价位仍须引用 `get_price_bars` 或 `get_indicators`；价位只引用经验的，同样作废。
- **按截止时间读**：只返回截止时间之前写入的版本（见[截止时间](system.cutoff.md)）；之后写的新版本、之后的归档都看不到。所以新经验不会回填到旧计划，引用指向的内容也永远不变。
- **草稿读不到**：未审定的教训草稿不在工具能读到的表里，也没有引用形式。
- **记录**：计划（首版或修订版）和复查通过校验后，程序把引用了哪条知识的哪一版、写在哪个字段（`thesis`、`invalidation`、`changes`、`counter`、`risks`、`review`）、由第几次工具调用返回，与计划或复查记录在同一个事务里写进 `knowledge_citations`。被反方过滤掉的条目、作废的运行、`pass` 的运行都不写。复查只有维持、作废或修订结论引用了知识时才写，记在那次复查上而不是某个计划版本上。
- 工具调用用到的 `get_knowledge` 返回里写明 `point_in_time: true` 和截止时间，并提示"这是用户审定过的经验，是判断不是事实；价位不能只引用它"。

## 在系统里哪里看到
计划卡和计划页的"引用的知识"（`know:<id>@<版本>` 加那一版的标题，点开看当时的版本；之后有新版会标"当前已是第 n 版"）；推理阅读视图里 `get_knowledge` 的调用与返回；条目页的"被引用"和[表现回看](review.knowledge_performance.md)。

## 注意
被引用多次不代表这条经验有效：计划引用它，只说明模型读到并用了它。表现回看是描述性统计，样本小且彼此重叠。
