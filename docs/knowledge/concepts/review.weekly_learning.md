---
id: review.weekly_learning
title: "你学到了什么"
group: review
version: 1
applies_to: [review-7, review-8]
summary: "周度复盘新增的一段：本周结束的交易的纪律四项、本周的复盘卡摘要、本周被检验的知识，随周度快照冻结，也随周度推送。"
related: [review.weekly, review.retro_card, review.knowledge_performance, system.my_knowledge, review.discipline_chase]
sources: [investment_assistant/review/outcomes.py, investment_assistant/review/period_digest.py, investment_assistant/notify/templates.py]
---

## 是什么
周度复盘里的"你学到了什么"把本周的复盘、你的审定和你积累的经验放在一起看：这周哪些交易结束了、犯了哪类纪律错误、复盘卡审了多少、哪条经验被后续的结果检验过。它和周度快照里的其他数字一起冻结，之后不重算。

## 怎么算 / 从哪来
快照的 `stats.learning` 有三项内容（`review-7` 起才有）：

- **纪律**：本周结束（结束日落在这一周）的[复盘卡](review.retro_card.md)上冻结的纪律四项合计：追高、过早止盈、死扛亏损、计划外，外加仍待定的项数。待定的项以后才能判定，这里不重算。纪律的定义见[追高进场](review.discipline_chase.md)等四张卡。
- **复盘卡**：本周结束的每张卡一行（标的、结束类型、你的执行（`review-7` 是你的决定）、实际 R 与规则、Agent 两条轨道的 R、已成立的标记、状态、一句话摘要），另有总数、已审定、待审定、起草中、起草失败的计数。
- **被检验的知识**：本周至少有一个样本的结果落在这一周的[我的知识](system.my_knowledge.md)条目，每条给出样本汇总（样本数、有结果数、均值），口径同[知识表现回看](review.knowledge_performance.md)：少于 5 个有结果的不给均值。

**推送**：周度推送多一个字段"你学到了什么"，依次写纪律一行、复盘卡计数一行、至多五张卡各一行、被检验的知识至多三条；三项都为空时只写一行"本周没有结束的交易，也没有被检验的知识"。字段超过 Discord 的 1024 字时从后面的明细截断。

## 在系统里哪里看到
「复盘 → 周度复盘」卡片里的"你学到了什么"一段：纪律一行、卡列表（链到卡页）、被检验的知识（链到条目页）。早于 `review-7` 的快照没有这一段，页面会明说，不重算。

## 注意
这些都是描述性统计，一周里的样本通常很小；"被检验"只是计划影子的结果落在了这一周，不说明这条经验有效。所有规则、Agent 与经验都未经验证。
