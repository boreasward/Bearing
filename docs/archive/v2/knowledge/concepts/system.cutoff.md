---
id: system.cutoff
title: "截止时间"
group: system
version: 1
applies_to: []
summary: "规则、计划和 Agent 的数据类工具只读截止时间之前已入库的数据（触发候选与持仓记录除外），每版计划的截止时间固定；这能还原当时所知，但不是严格的历史回测。"
related: [system.append_only, agent.tool_calls, agent.version_chain, system.concept_card]
sources: [investment_assistant/analysis/agent/tools.py, investment_assistant/execution/timeline.py, investment_assistant/analysis/agent/runner.py, investment_assistant/core/tx.py]
---

## 是什么
每一次批处理、每一次 Agent 运行、每一版计划都有一个**截止时间**：只有在这个时刻之前已经写进数据库的数据，才允许参与计算。这样事后回看某份计划，就能知道它当时依据的是什么，而不是被后来补进来的数据"污染"。

## 怎么算 / 从哪来
- **批处理**：截止时间是这次运行的开始时刻；规则、计划、Agent 轨道读到的行都要求入库时间不晚于它。
- **Agent 工具**：不接受日期参数，截止时间由批处理装进工具，模型改不了（见[工具调用](agent.tool_calls.md)）。价格、申报、资讯、财报日和市场环境类工具按截止时间过滤；`get_trigger` 读当日候选、`get_position` 读持仓记录，不按截止时间过滤。
- **版本链的每一版**：各有固定的截止时间（生成时刻）；每日复查的截止时间是批处理开始时刻，你点"重新复查"则是点击的那一刻。
- **资讯**：发布时间未知时，页面写"发布时间未知"，不用获取时间代替。
- 数据被修订（如日线数值更新）会追加新版本行，旧值保留，按截止时间读到的是当时最新的那一行（见[只追加与更正](system.append_only.md)）。

**唯一的例外**是概念卡：它是定义而不是数据，不按截止时间回读，见[概念卡](system.concept_card.md)。

## 在系统里哪里看到
计划块的"生成于……截止……"；版本链每版的截止时间；推理阅读视图里每次工具调用的参数与返回值。

## 注意
这**不是严格的历史时点回测**：免费数据源（如日线）的历史数据会被事后修订，系统只能保证"开始采集之后"的记录可按时点还原。截止时间保证的是"不偷看之后入库的数据"，不保证数据本身正确。
