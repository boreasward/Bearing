---
id: agent.review_when
title: "下次复查条件"
group: agent
version: 2
applies_to: [agent-2, agent-3, condition-1, condition-2]
summary: "Agent 计划可以写 0 到 3 条复查条件，满足时当天必须认真复查；留空等于每个交易日都复查。"
related: [agent.invalidation, agent.review_outcome, agent.signals, agent.changes]
sources: [investment_assistant/execution/conditions.py, investment_assistant/execution/plan_check.py, investment_assistant/execution/timeline.py]
---

## 是什么
复查条件是 Agent 告诉系统"在什么情况下请再叫我看一次这份计划"。它和[失效条件](agent.invalidation.md)用同一张 `condition-2` 登记表，区别是：失效条件成立计划就作废，复查条件成立只是意味着当天要让模型认真复查。

可用于复查的 `kind` 在 `condition-2` 下共十种（与失效条件有重叠；`condition-1` 下的 `agent-2` 计划只用前九种，没有 `held_sessions_reached`）：

| `kind` | 参数 | 文案模板 |
| --- | --- | --- |
| `close_below` | `price` | 收盘跌破 {price} |
| `close_above` | `price` | 收盘突破 {price} |
| `earnings_within` | `sessions` | 距财报不足 {sessions} 个交易日 |
| `regime_is` | `label` | 区制变为 {label} |
| `sector_weak` | 无 | 所属行业转弱 |
| `rs_below` | `window`、`value` | 相对 SPY {window} 日强弱低于 {value} |
| `new_filing` | `form` | 出现新的 {form} |
| `date_reached` | `date` | 到 {date} 再看 |
| `next_session` | 无 | 下一交易日 |
| `held_sessions_reached` | `sessions` | 持有 {sessions} 个交易日仍未到 1R |

`date_reached` 和 `next_session` 只能做复查条件；`held_sessions_reached`（`sessions` 为 3 到 30，入场期恒为未成立）模型只能写在这里，失效条件里的同类条件由宿主注入；`no_entry_by` 和 `no_entry_before_earnings` 只能做失效条件。

## 怎么算 / 从哪来
- **条数**：0 到 3 条，留空等于只有一条 `next_session`。参数范围与失效条件相同，复查条件不要求带引用。
- **判定窗口**：上次有效复查之后到复查日。结果同样是 `met`、`not_met`、`unknown` 三种，数据缺失为 `unknown`。
- **用法**：当天如果没有任何[系统信号](agent.signals.md)触发、没有新的用户输入、所有复查条件都没满足，系统规则直接维持原计划，理由写"无新信号，复查条件未满足（条件文案）"，不调用模型。反过来，只要有一个复查条件成立，就要让模型认真复查。
- 因为 `next_session` 每个交易日都成立，留空的计划每天都会被认真复查一次。

## 在系统里哪里看到
计划块的"下次复查条件"（空时显示"下一交易日"）；计划页复查记录里每条复查列出各条件当天的判定。

## 注意
复查条件越具体，系统规则维持的天数越多、模型调用越少；但这是 Agent 自己的选择，系统不评价它选得好坏。

这条口径同样**未经验证**（Phase 0 Gate HOLD，见[证据状态「未验证」](system.gate_hold.md)）：它只说明系统怎么运作，不说明这些条件有效。
