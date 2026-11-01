---
id: account.reconciliation
title: "券商快照对账"
group: account
version: 1
applies_to: [account-1]
summary: "券商快照与同日或之前最近交易日的账本权益比较，差额同时不超 1% 与 100 美元才算一致；对账只提示，不改账。"
related: [account.ledger, review.monthly_return, review.position]
sources: [investment_assistant/workbench/accounts.py, investment_assistant/workbench/account.py, docs/specs/2026-10-31-v2.11-accounts-and-paper-trading-design.md]
---

## 是什么

对账把你录入的券商总资金快照与内部[账本权益](account.ledger.md)并排比较，用来发现漏录成交、出入金、费用或估值日期不一致。快照是外部观察值，不能自动覆盖账本。

## 怎么算 / 从哪来

每条有效快照取 `as_of` 当天或之前最近一个交易日的最新权益行：

- `diff` = 券商快照权益 − 账本权益。
- `pct` = `diff` ÷ 账本权益。
- 默认容差：**绝对差额 ≤ 100 美元，并且相对差额 ≤ 1%**，两项都满足才是 `matched`（对账一致）；超过任意一项就是 `mismatch`（待核对）。容差可通过账户参数调整，使用差额的绝对值判断。
- 没有可用权益行是 `no_ledger`（暂无账本权益）。它既可能是未开账，也可能是已经开账、但快照日期之前尚无权益行，不能只凭此状态认定账户未开账。

账本权益为 0 且差额非 0 时，相对差额无有效分母，仍标待核对。更正过的快照只看最新有效记录；对账只生成读数与提示，不补成交、不改现金、不重写权益。

## 在系统里哪里看到

真实账户的「概览」看对账块，先核对快照日期、所用账本交易日，再查[成交与持仓](review.position.md)、出入金、费用和缺行情提示。差额消失之前，把它作为待解释问题；不要仅为让数相等而录一笔没有依据的现金变化。
