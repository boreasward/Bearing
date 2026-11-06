---
id: review.position
title: "持仓与成交"
group: review
version: 1
applies_to: []
summary: "你录入的成交按先进先出配对出持仓和已平仓批次，买入费用计入成本、卖出费用按数量分摊、跨拆股按比例换算；录错只能作废。"
related: [system.decision, review.discipline_unplanned, plan.trailing_stop, system.append_only]
sources: [investment_assistant/execution/positions.py, investment_assistant/execution/ledger.py, investment_assistant/execution/exits.py]
---

## 是什么
系统不接券商，持仓来自你自己录入的成交。每笔成交记录标的、买卖方向、数量、价格、费用、成交时间，并可关联当时的决定。

## 怎么算 / 从哪来
- **先进先出**：卖出时从最早的买入批次开始配对，得到已平仓段和仍持有的批次。
- **费用**：买入费用摊入每股成本；卖出费用按配对数量的比例分摊，从卖出所得中扣除。
- **拆股**：跨拆股时，持有数量和每股成本按拆股比例换算，让成本能与拆股调整后的日线直接比较。
- **作废成交**：作废的成交不再计入持仓。成交写入后不改，录错就"作废"并写理由，再重新录入（见[只追加与更正](system.append_only.md)）。
- **校验**：成交时间必须带时区、不能晚于当前时刻；卖出数量不能超过当前持仓。
- **持仓的计划**：取"当前未平仓批次中最近一笔、且其决定关联了计划的买入"所关联的计划；有计划的持仓用计划止损与计划目标判断，没有计划的用持仓回撤。见[移动止损](../../../../knowledge/concepts/plan.trailing_stop.md)。

每个已平仓段还显示持有交易日数、盈亏、同期 SPY、最大浮盈和最大浮亏。

## 在系统里哪里看到
「复盘 → 交易与持仓」；「今日」的持仓行。

## 注意
持仓是你录入的结果，漏录或错录会直接影响持仓回撤、计划止损等规则对持仓的判断。
