---
id: system.append_only
title: "只追加与更正"
group: system
version: 1
applies_to: []
summary: "业务记录写入后不改不删：数据修订追加新版本，计划不覆盖，决定、输入、快照的更正都是新记录指向旧记录；录错的成交只能作废。"
related: [system.cutoff, agent.user_input, system.decision, review.position]
sources: [investment_assistant/execution/repo.py, investment_assistant/execution/ledger.py, investment_assistant/execution/user_inputs.py]
---

## 是什么
系统把"发生过什么"当作不可篡改的记录。这样任何一个历史结论都能被复核：当时的输入是什么、算出了什么、你做了什么决定。

## 怎么算 / 从哪来
- **数据修订**：日线等数据源改了数值，就追加一个修订版本，不覆盖旧值。
- **计划**：同一单元重跑，结果相同就返回原计划；结果不同则记入运行报告的冲突列表，不覆盖原计划。Agent 的版本链也是追加：旧版写入后不再改动，修订产生新的一版。
- **更正 = 新记录指向旧记录**：决定（`supersedes_id`）、用户输入（更正和撤回）、资金快照、周度和月度复盘快照，都是这样。旧记录继续可读。
- **成交**：录错就"作废"并写理由，再重新录入。作废是在原记录上标注作废时间与理由，行本身保留。
- **证据**：每个阶段的运行证据、观察池分层变化、工具调用都是只追加的表。

## 在系统里哪里看到
计划页的版本链、决定历史、"我的补充"里的已更正 / 已撤回、周度复盘的更正快照、「运行一览」的阶段证据。

## 注意
只追加意味着错误也会留下痕迹，这是有意的；你看到"已更正"或"已作废"，是在看被保留下来的完整经过，而不是系统出了问题。
