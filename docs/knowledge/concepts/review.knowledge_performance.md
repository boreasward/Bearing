---
id: review.knowledge_performance
title: "知识表现回看"
group: review
version: 1
applies_to: [knowledge-perf-1, plan-shadow-1]
summary: "按引用过这条知识的计划版本的影子结果回看：每个单元取最早引用的一版，少于 5 个有结果不给均值，复查中的引用单列。"
related: [system.my_knowledge, review.plan_shadow, review.r_multiple]
sources: [investment_assistant/review/knowledge/perf.py, investment_assistant/review/outcomes.py]
---

## 是什么
表现回看回答："引用过这条知识的计划，后来大致怎么样？"它是描述性统计，不是对这条经验有效性的检验。

## 怎么算 / 从哪来
- **样本单位是单元**：同一条链（标的加信号日）修订多次、每版都引用了这条知识，只算一个样本，取最早引用它的那一版计划，不让后来的修订把结果修好看。
- **结果**：用[计划影子](review.plan_shadow.md)模拟这份计划，口径和计划影子页同一个函数、同一个阈值（`plan-shadow-1`）；汇总给出样本数、有结果数、过期数，以及 [R](review.r_multiple.md) 的均值和中位数。
- **样本不足**：有结果的少于 5 个时不给均值和中位数，只写"样本 n 个（有结果 m 个），少于 5 个不给均值"。
- **按版本看**：同一条知识的不同版本各被多少份计划、多少个单元引用。
- **复查中的引用单列**：复查结论引用了知识但没有产生新版计划的，只统计次数，不进样本，因为没有新价位可以模拟。

## 在系统里哪里看到
[我的知识](system.my_knowledge.md)条目页的"表现回看"：样本行常驻在最上方，其下是每个单元一行的明细和按版本的统计。

## 注意
样本小、不同单元的信号常常同日重叠，引用这条知识的计划也可能同时受其他因素影响；结果不能证明这条经验有效或无效。
