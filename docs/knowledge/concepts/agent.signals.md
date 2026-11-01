---
id: agent.signals
title: "系统信号"
group: agent
version: 2
applies_to: [timeline-1, timeline-2]
summary: "复查时系统自己检查八项信号：新 8-K、异常波动、触及止损或 T1、临近到期、区制变化、新用户输入、再次触发；窗口是上次有效复查之后。"
related: [agent.review_outcome, agent.review_when, agent.user_input, rule.abnormal_move, review.chain_state]
sources: [investment_assistant/execution/timeline.py]
---

## 是什么
信号是系统在复查前先做的一轮机械检查，回答"自从上次看过这份计划，发生过什么值得再看的事"。共八项（`timeline-1` 的旧复查记录只有前七项），不论是否触发都会写进复查记录：

| 信号 | 含义 |
| --- | --- |
| `new_8k` | 出现新的 8-K 申报 |
| `abnormal_move` | 出现[异常波动](rule.abnormal_move.md)候选 |
| `stop_touched` | 日线最低价触及最新版的止损 |
| `t1_touched` | 日线最高价触及最新版的 T1 |
| `expiry_near` | 入场期，且次一交易日恰好是最新版的截止日 |
| `regime_change` | 区制标签和最新版生成时冻结的标签不同 |
| `user_input` | 出现了新的[用户输入](agent.user_input.md) |
| `retrigger` | 同一标的又触发了买入信号，已并入这条活跃链（v2.9 起；按记录写入时间落在窗口内判定） |

## 怎么算 / 从哪来
- **窗口**：上次有效复查（维持、修订或作废）之后到复查日；还没复查过则从该版生成时算起。跳过和出错的复查不会移动窗口。
- `new_8k` 按 SEC 受理时间判断；没有受理时间的，按申报日晚于窗口起点判断。申报列表不可用时记"未知"，不当作触发。
- `expiry_near` 在持仓期不适用。
- `retrigger` 取 `chain_retriggers` 里在窗口内写入的行，证据带触发的规则、冻结取值与当天规则基线价位。
- `regime_change` 任一侧缺标签或为"样本不足"时记"未知"，不当作触发。

任何一项触发，当天就不能由系统规则直接维持，必须让模型认真复查（见[每日复查结论](agent.review_outcome.md)）。信号本身不改计划。

## 在系统里哪里看到
计划页复查记录里"触发的信号"；「今日」"进行中的 Agent 计划"每行的今日触发信号。

## 注意
信号只说明"发生了某件事"，不判断它对计划是好是坏。"未知"表示数据不够判断，并不表示没发生。

这条口径同样**未经验证**（Phase 0 Gate HOLD，见[证据状态「未验证」](system.gate_hold.md)）：它只说明系统怎么运作，不说明这些信号有效。
