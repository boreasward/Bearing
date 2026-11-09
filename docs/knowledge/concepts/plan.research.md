---
id: plan.research
title: "研究与跟踪"
group: plan
version: 1
applies_to: [plan-1]
summary: "用户点名一只股票写下论点，agent 当场给结论三选一；「值得盯」落一个宿主能查的条件，每天由宿主检查，条件到了才让 agent 复看；转成的计划不占待入场名额。"
related: [plan.status, plan.entry_type, plan.validity]
sources: [investment_assistant/execution/research.py, investment_assistant/facts/research_review.py, investment_assistant/facts/research_repo.py]
---

## 是什么
研究是一条独立的记录（`v3_research`），不是计划的一个状态：用户在对话里点名一只股票说出论点，agent 研究后给结论，系统按结论推进。状态五种，后四种是终态：

| 状态 | 代码 | 怎么到这 |
| --- | --- | --- |
| 观察中 | `watching` | 用户发起（对话）；一只股票同时最多一份观察中的研究（数据库部分唯一索引保证） |
| 已转计划 | `planned` | agent 结论「可以出计划」并保存了计划（计划记 `research_id`，研究记 `plan_id`） |
| 已否决 | `rejected` | agent 结论「否决」 |
| 已过期 | `expired` | 有效期内条件未到，或一直没有结论 |
| 已放弃 | `dropped` | 用户放弃（对话或页面） |

## 怎么算 / 从哪来
- **发起**（对话工具 `start_research`，先复述论点、`confirmed` 为真才写、按 `request_id` 幂等）：建记录、记一行 `created` 变化记录；该股票已有有效[计划](plan.status.md)或观察中的研究时拒绝并返回那份。有效期从发起日起 `v3.research_sessions` 个交易日（缺省 20）。
- **结论三选一**（`conclude_research`）：`ready`（照常保存计划）/ `watch`（必须带一个条件；对话里条件由 agent 提出、用户确认后才写，有效期从当天重算）/ `reject`。简报会话里复看给 `watch` 不能带新条件（没有用户在场确认，被拒 `condition_needs_user`），只能保留原条件。
- **条件四种**，宿主用日线就能检查：`close_above`（收盘站上 {level}）、`close_below`（收盘跌破 {level}）、`touch_below`（最低价触及 {level} 或以下）、`close_above_ma`（收盘站上 20 / 50 / 200 日均线；日线不足该根数写「日线不足，未检查」）。取值由 agent 从论点里提、用户确认。
- **每日复查**（宿主算，不调模型，每日任务第 3 步）：从复查到的交易日的下一个交易日逐日补查到当天；当天缺日线不判、不推进；条件到了记一次 `triggered`（重跑不重复）；日期到或过了 `valid_until` 且没触发 → `expired`。
- **复看**（agent）：条件已到的研究进当天待办（`get_agenda` 的 `research` 类）；简报模式的范围在名单前 N 只之外加上待办里的研究股票。研究股票同时在名单上时名单行标「研究中」，不进二次确认清单、不进替换候选。
- **研究转成的计划**走同一个 `save_plan`，区别三处：不占 3 个待入场名额（名额与替换的受害者只数规则计划）；[入场类型](plan.entry_type.md)可用 `pullback`；agent 自报价位时入场下限从「最新收盘 − 2 ATR」放宽到「最新收盘 − `v3.research_entry_atr` × ATR」（缺省 4），修订同一份计划用同一个放宽下限。其余纪律（一只股票一份有效计划、止损、账户、股数）全部照旧。
- **采集**：观察中研究的股票每天并进采集目标（与有效计划同一处），**不写关注列表**——关注列表是筛选范围，研究不改名单。
- **变化记录**（`v3_research_events`，只追加）：`created` / `concluded` / `triggered` / `planned` / `rejected` / `expired` / `dropped` 各记一行；宿主事件按（研究、交易日、类型）唯一，重跑不重复。

## 在系统里哪里看到
页面的「研究」卡片组（观察中、已结束两组，可展开时间线、可放弃、「发起研究」按钮预填助手输入框）；简报的「研究」段（每份观察中的研究一行：第几天、论点、条件、最新收盘、状态）与「和昨天比变了什么」的研究行；Discord 摘要里研究条件到了的一行。

## 注意
- 存了 `watch` 结论而没有条件、或 `ready` 之后没保存计划：研究仍是「观察中、无条件」，宿主每天不检查它，到[有效期](plan.validity.md)自然过期。
- agent 没有用户确认不能改研究的条件；研究不写关注列表、不改筛选范围。这些口径**未经验证**。
