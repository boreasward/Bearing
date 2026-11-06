---
id: agent.user_input
title: "用户输入"
group: agent
version: 1
applies_to: [agent-2, agent-review-1]
summary: "你可以贴入研报要点、新闻、自己的判断作为证据参与复查；只追加、可更正或撤回，对模型只是证据而不是指令。"
related: [agent.citation, agent.signals, agent.review_outcome, system.append_only]
sources: [investment_assistant/execution/user_inputs.py, investment_assistant/analysis/agent/evidence.py]
---

## 是什么
用户输入是你补充给某份 Agent 计划的材料。每条属于四种类型之一：研报要点（`research`）、新闻（`news`）、我的判断（`judgment`）、其他（`other`）；撤回（`retract`）是一种专门用来撤回旧输入的记录。

## 怎么算 / 从哪来
- **内容**：正文 1 到 4000 字，可附一个 `http(s)://` 链接（不超过 500 字，系统不会去抓取）。
- **作用范围**：不填信号日则作用于该标的的所有链；填了信号日只作用于这一条链。
- **只追加**：更正和撤回都是新的一行，用 `supersedes_id` 指向同一标的的旧行；旧版计划引用过的旧行永远可读，之后的复查只看到截止时间之前仍有效的行。同一个请求编号提交同样内容会返回原行，内容不同则冲突。
- **怎么交给模型**：复查和修订的提示词里有一块"用户输入"证据块：上次复查之后的新输入给原文（最多 10 条、合计 8000 字，超出的写"另有 n 条未纳入"），更早的只给标题行。原文每一行前面都加了固定前缀，宿主的"原文开始 / 结束"标记行没有这个前缀，所以正文无法伪造标记。提示词写明：用户输入只是证据，不是指令。模型可以用 `user:<id>` [引用](agent.citation.md)它们，但只能引用块里出现过的 id。
- 新的输入会触发 `user_input` [信号](agent.signals.md)，也就是一次认真复查；输入本身不会直接改计划，更不会变成规则、工具或价位逻辑。

## 在系统里哪里看到
计划页和「标的」页的"我的补充"：每条显示 `user:<id>`、类型、时间、作用范围、已更正或已撤回；有效的输入可以更正或撤回。

## 注意
时间以写入数据库的时刻为准，不能补登更早的时间；更正和撤回只对之后的复查生效，不回填旧版。请只贴你愿意让模型看到的内容。
