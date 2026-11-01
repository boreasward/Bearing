---
id: review.holding_status
title: "持仓状态"
group: review
version: 1
applies_to: [timeline-2, agent-review-2]
summary: "每个持仓标的每个复查交易日一行：持有天数、距止损与 T1、现在几 R、指令（持有/卖一半/退出/无计划）和维持理由，复查失败那天也照写。"
related: [plan.max_hold, agent.retrigger, system.active_chain, review.chain_state, review.r_multiple]
sources: [investment_assistant/execution/timeline.py, investment_assistant/execution/chains.py, investment_assistant/execution/repo.py, docs/features/daily-plan.md]
---

## 是什么
"我手上这只现在该继续拿还是该卖？"每个持仓标的每个复查交易日有一行持仓状态（表 `holding_status`，只追加），由每日复查在全部复查之后写入。**不论复查成功、跳过、出错、Agent 不可用，也不论有没有对应的链、链是否已进入退出期，当天都有一行**——这是"每天都有一行"的保证；`plan_review check` 每天核对。

## 怎么算 / 从哪来
| 字段 | 计算 |
| --- | --- |
| 数量、成本 | 当时未平批次（只含截止时间之前成交并录入的成交） |
| 有效止损与模式 | 有链取**链最新版**的止损（持仓期修订上移的止损随之生效），无链取成交关联的计划；T1 之后为移动止损（模式 `trailing`）。1R 的风险取第一笔关联买入所关联版本的止损 |
| 距止损、距 T1 | (收盘 − 止损) ÷ 收盘，(T1 − 收盘) ÷ 收盘，百分比；已跌破为负，已触及 T1 时距 T1 为空 |
| 持有天数、剩余 | 见[最长持有](plan.max_hold.md)；已到 1R 或 T1 后剩余为空 |
| 现在几 R | (收盘 − 成本) ÷ R |
| 指令 | `exit`（次一交易日开盘卖出：当天结论为持仓期作废，或链在退出期）、`sell_half`（到 T1：卖出一半，余下按移动止损）、`hold`（继续持有）、`no_plan`（无计划持仓，回撤规则看护） |
| 退出原因 | `stop_touched` 止损触及、`time_stop` 时间止损、`invalidation` 失效条件成立、`agent_invalidate` Agent 判作废 |

维持理由：Agent 复查维持时是它的理由；宿主维持时是"无新信号，复查条件未满足（…）；收盘 x 在止损上方 y%，未到 T1，持有 n/m 天"；复查没做完是"今日复查未完成（<原因>），按第 n 版继续持有"；无链持仓是"无 Agent 链，按 规则基线 / 自定计划 的止损与最长持有看护"；退出期是"退出指令自 <日期> 起（<原因>），仍持有 n 股"。缺当天日线时收盘相关数字为空，已有的退出指令不会因缺数据而解除。

## 在系统里哪里看到
「今日」持仓组一行"MU 持有 4/10 天 · 距止损 −5.20% · 距 T1 +8.10% · 现 +0.6R · 继续持有：…"；推送的持仓消息（维持也推，退出行前加 ⚠）；计划页的持仓状态小表。

## 注意
- 同一 session 重跑不写新行（先写的为准）；重新复查不会改当天的行，页面在旁边显示之后的结论。
- 无链持仓与同标的新入场链并存时，持仓状态按标的一行（全部未平批次），链上的买入按链，两边的股数可能不同。
- 这些是规则算出的提示，**未经验证**；系统不替你下单。
