---
id: plan.earnings
title: 财报规则与下次财报日
group: plan
version: 1
applies_to: [plan-1]
summary: "有效期内有财报 → 计划作废；之后 10 个交易日内有财报，或财报日未知 → 加提示。"
related: [plan.validity, plan.status, rule.earnings_gap_up, family.sec_filings]
sources: [investment_assistant/execution/plan_levels.py, investment_assistant/data/adapters/earnings.py, docs/features/daily-plan.md]
---

## 是什么

财报公布会让股价大幅跳空，止损在跳空面前形同虚设。所以计划对财报很保守：有效期内要发财报就不建议入场；财报离得近或日期不确定，则提醒你注意。

## 怎么算 / 从哪来

下次财报日来自 yfinance 的财报日历，状态为「已知」时才有日期；取不到就记「未知」，系统**绝不编造日期**。按信号日 T 判断：

| 情形 | 结果 |
| --- | --- |
| 财报日落在有效期（T+1 至 T+3）内 | 计划作废，原因 `earnings_in_validity` |
| 财报日在有效期之后、且在 T 之后 10 个交易日内 | 提示 `earnings_within_10d` |
| 财报日未知、已经过去，或日历取不到 | 提示 `earnings_unknown` |
| 其余 | 无 |

## 在系统里哪里看到

- 计划块的提示与作废原因；关注列表的「下一事件」列；推导视图里输入快照的财报行。

## 注意

- 作废原因和提示是两回事：作废的计划不可执行，提示只是提醒，价位与有效性不变，见[计划状态](plan.status.md)。
- 在你自己提交价位的[自定计划](plan.own.md)里，作废原因降级为提示。
- 财报日历来自免费来源，可能不准；规则**未经验证**（Phase 0 Gate HOLD）。
