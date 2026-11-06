---
id: review.first_latest
title: "首版与最新版汇总"
group: review
version: 1
applies_to: [review-6, monthly-3, review-8, monthly-4]
summary: "汇总 Agent 计划的结果时，agent 口径每条链只算首版，agent_latest 只算最新版，避免样本被修订次数放大；有 R 的不足 5 份不给均值。"
related: [review.weekly, review.version_basis, agent.version_chain, review.plan_shadow]
sources: [investment_assistant/review/outcomes.py, investment_assistant/review/monthly.py]
---

## 是什么
一条 Agent 链如果被修订了三次，就有四个版本，每个版本都做影子跟踪。如果把它们都当作独立样本，Agent 的样本数会被修订次数放大。所以汇总时分两个口径：

- **首版（`agent`）**：每条链只算第 0 版。
- **最新版（`agent_latest`）**：每条链只算当时的最新版。

`agent-1` 旧版没有链，每份计划既算自己的首版，也算自己的最新版。

## 怎么算 / 从哪来
每个口径给出：计划份数（`count`）、有 R 的份数（`determined`）、到期未成交（`expired`）、R 的均值与中位数。**有 R 的不足 5 份时，均值和中位数为空**，不用太少的样本给出一个看似精确的数字。

周度复盘和月度复盘的计划影子都按这两个口径汇总；`review-5` 之前的周度快照、`monthly-2` 之前的月度快照里，`agent` 是每一版都算，页面仍标"Agent"，已冻结的快照不会重算。

## 在系统里哪里看到
周度、月度复盘的计划影子（标"Agent（首版）"与"Agent（最新版）"）；计划影子接口的 `agent_summary` 里的 `first` 与 `latest`。

## 注意
首版衡量的是 Agent 一开始的判断，最新版衡量的是经过修订后的计划；两者并列才能看出修订是否真的有帮助，但样本很小，不宜过度解读。
