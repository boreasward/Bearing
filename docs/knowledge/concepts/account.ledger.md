---
id: account.ledger
title: "账户账本"
group: account
version: 1
applies_to: [account-1]
summary: "账本按本账户的初始现金、出入金和成交算现金，按先进先出算持仓，再用最近收盘估值；券商快照只用来对账。"
related: [account.reconciliation, account.comparison, review.position, review.monthly_return]
sources: [investment_assistant/workbench/accounts.py, investment_assistant/workbench/positions.py, docs/specs/2026-10-31-v2.11-accounts-and-paper-trading-design.md]
---

## 是什么

账本用本账户已记录的现金变化和成交，算出现金、持仓和权益（现金加持仓市值）。真实账户由你录入成交，模拟账户由程序记录[模拟成交](account.fill_rules.md)；两个账户的记录分别计算，券商资金快照只作为[对账](account.reconciliation.md)的外部参照。

## 怎么算 / 从哪来

| 量 | `account-1` 口径 |
| --- | --- |
| 现金 | 初始现金 + 未作废净出入金 − 买入的（股数 × 价格 + 费用）+ 卖出的（股数 × 价格 − 费用） |
| 持仓数量与成本 | 未作废成交按成交时间先进先出；买入费用计入成本，卖出费用扣减所得，拆股同步换算数量与每股成本 |
| 持仓市值 | 各持仓数量 × 截止日或之前最近收盘，再求和 |
| 权益 | 现金 + 持仓市值 |
| 累计已实现盈亏 | 已匹配平仓批次的卖出净所得 − 买入成本之和 |
| 当日盈亏 | 今日权益 − 上一权益行的权益 − 两行之间净出入金；没有上一行时为空 |

只计截止时间内的成交与出入金，作废记录不计。真实账户开账要填开户日和**大于 0 的初始现金**；已有持仓可按数量与每股成本一并登记，作为实物转入，**不从现金中再扣一次成本**，即使带入持仓，初始现金仍须大于 0。

缺当天收盘时，沿用之前的收盘并同时记 `bars_missing` 与 `stale` 的日期。连历史收盘也没有时，内部该标的市值暂计 0 并标缺数据，权益因此不完整，不能把这个 0 当成实际市值。

## 在系统里哪里看到

「账户 → 概览」看现金、持仓市值、权益与盈亏；「持仓」「成交」追溯批次与来源。每日权益行保存 `account-1` 和估值明细；看到缺行情提示时，先核对收盘日期，再读收益与回撤。
