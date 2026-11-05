---
id: rule.earnings_gap_up
title: 财报跳空向上
group: rule
version: 1
applies_to: [rules-1]
summary: "买入候选：8-K Item 2.02 财报发布后的第一个交易日，开盘较前收盘跳空 ≥ +3%，且收盘不低于开盘。"
related: [rule.earnings_gap_down, indicator.gap, family.sec_filings, rule.new_filing]
sources: [investment_assistant/analysis/rules.py, investment_assistant/derivation/chain.py, docs/archive/v2/features/daily-plan.md]
---

## 是什么

财报超预期时，股价常在次日直接高开，并且当天没有被卖回去。这条规则把「财报后跳空高开且收盘站得住」的股票挑出来，动作是**买入**。

## 怎么算 / 从哪来

- 事件：公司提交的 8-K 含 Item 2.02（业绩公告）。
- 看哪一天：按 SEC **受理时间**换算到纽约时间——盘后（16:00 之后）或非交易日发布算下一个交易日，盘前（9:30 之前）发布算当天，**盘中发布不触发**本规则。
- 条件：该日开盘较前一日收盘的跳空 `gap` ≥ +3%（阈值 0.03，见[跳空幅度](indicator.gap.md)），且当日收盘 ≥ 开盘。
- 候选里冻结 `open`、`close`、`prev_close`、`gap` 和对应的申报 id。

## 在系统里哪里看到

- 「今日」决策单元的候选规则与「触发依据」。

## 注意

- 受理时间缺失时无法确定是哪一天，只记跳过原因 `earnings_time_unknown`；批处理会用 SEC 列表补全缺失的受理时间，只补空值，不覆盖，见[SEC 申报](family.sec_filings.md)。
- 同一份财报也会触发[新财报](rule.new_filing.md)观察；价格前置条件同其它规则。
- 规则**未经验证**（Phase 0 Gate HOLD）：跳空后是否延续没有被检验过。
