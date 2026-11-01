---
id: review.monthly_return
title: "月度收益与分档"
group: review
version: 2
applies_to: [monthly-3, monthly-4, monthly-5]
summary: "月度收益优先用两个月末最后交易日的账本权益，扣除区间净出入金；未开账或缺任一端点时回退券商快照，注明来源。"
related: [account.ledger, account.reconciliation, account.comparison, review.process_check, review.sensitivity, review.weekly, review.position]
sources: [investment_assistant/workbench/monthly.py, investment_assistant/workbench/accounts.py, investment_assistant/config.py]
---

## 是什么
月度收益衡量整个账户一个月扣除出入金后权益变化的比例。`monthly-5` 优先读取[账本权益](account.ledger.md)，未开账或月末端点不全时回退到券商资金快照；两种来源必须分清。

## 怎么算 / 从哪来
- **账本公式**：月度收益 =（本月末权益 − 上月末权益 − 区间净出入金）÷ 上月末权益，`source = ledger`。两端必须分别是本月与上月**最后一个交易日**的最新有效权益行，不拿月内更早的一行顶替。净出入金只计上月末交易日之后、本月末交易日之内的未作废记录，入金为正；上月末权益不大于 0 时不计算收益率。
- **回退快照**：真实账户未开账、缺任一账本端点，或任一端点最新行的 `detail.bars_missing` / `detail.stale` 非空时，使用原 `monthly-4` 公式：（本月最后有效快照 − 上月最后有效快照 − 两个快照日之间净出入金）÷ 上月快照，`source = snapshot`；不因最新行行情不完整而改取同日旧版本。每端只能取相应月份的有效快照，更早月份不能顶替；任一端仍缺则显示「缺资金快照，无法计算」，不算成 0 或跨月份收益。
- **分档**：大于 20% 为非常优秀；10% 到 20%（含 10%）为优秀；0 到 10%（含 0）为可行；小于 0 为需关注。阈值是配置项，默认如此。
- **对比**：同时列出同期 SPY 收益和超额收益。

券商快照在开账后主要用于[对账](account.reconciliation.md)；更正必须指向当前有效快照，出入金按笔录入、录错只能作废。历史已冻结的月度复盘保留生成时的版本与来源，不因新口径重算旧快照。

## 在系统里哪里看到
「复盘 → 月度复盘」看完整月份的收益、来源、档位和同期 SPY；月度复盘与推送仍只处理真实账户。核对结果时先看 `monthly-5`、`source` 和两个端点日期，再查出入金。「账户」页的[本月至今收益](account.comparison.md)截至当前 session，由读视图独立计算，不采用未来月末端点。

## 注意
分档只是便于一眼读数的标签，不是对决策水平的评价；一个月的收益受仓位、市场和出入金时点影响很大。
