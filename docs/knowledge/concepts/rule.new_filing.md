---
id: rule.new_filing
title: 新财报
group: rule
version: 1
applies_to: [rules-1]
summary: "观察候选：当日出现对你而言是新的 10-Q / 10-K / 8-K 申报；它不看价格，只提醒你去读。"
related: [rule.earnings_gap_up, rule.abnormal_move]
sources: [investment_assistant/analysis/rules.py, investment_assistant/derivation/chain.py, docs/archive/v2/features/daily-plan.md]
---

## 是什么

公司出了新的季报、年报或重大事项公告，是你应该去读一眼的时刻。这条规则不判断好坏，只在当天产生一个**观察**候选。

## 怎么算 / 从哪来

- 申报表单类型是 10-Q、10-K 或 8-K 之一。
- 「对用户是新的」按申报进入你视野的那个交易日算：有受理时间的，盘后（16:00 之后）或非交易日发布算下一个交易日；缺受理时间的用申报日。
- 当日所有符合的申报合并成一个候选，冻结表单列表和申报 id。

## 在系统里哪里看到

- 「今日」决策单元里的「新财报」候选；「标的」页的 SEC 列表可以看到具体文件，见SEC 申报。

## 注意

- 它**不需要价格数据**：即使日线不足 200 根，也能触发。
- 有新财报的当天，[异常波动](rule.abnormal_move.md)不再触发——这天的大幅波动先归因于财报。
- 财报引发的跳空另有[财报跳空向上](rule.earnings_gap_up.md)等价格规则。观察候选只提醒，不生成计划；规则**未经验证**。
