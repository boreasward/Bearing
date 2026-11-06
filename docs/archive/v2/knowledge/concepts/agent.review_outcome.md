---
id: agent.review_outcome
title: "每日复查结论"
group: agent
version: 1
applies_to: [agent-review-1, timeline-1]
summary: "每天收盘后对每条入场期或持仓期的链复查一次，结论为维持、修订或作废，由系统规则或 Agent 判定；还可能是跳过或出错。"
related: [agent.signals, agent.invalidation, agent.review_when, agent.version_chain, agent.user_input]
sources: [investment_assistant/analysis/agent/runner.py, investment_assistant/execution/timeline.py, investment_assistant/execution/plan_check.py, investment_assistant/analysis/agent/graph.py]
---

## 是什么
批处理第七个阶段 `review`（每日复查）对每条有效的 Agent 计划链做一次复查，每条链每个交易日至多一条有效的批处理复查记录。结论有三种：

- 维持（`maintain`）：计划不变。
- 修订（`revise`）：产生新的一版。
- 作废（`invalidate`）：计划不再有效，链结束。

另外两种记录不算有效复查：跳过（`skipped`）和出错（`error`）。每条记录写明判定方：**系统规则**或 **Agent**。

## 怎么算 / 从哪来
按固定顺序判定，前面的成立就不看后面：

1. **宿主作废**：任一[失效条件](agent.invalidation.md)成立，系统规则作废，理由"失效条件成立：条件文案"，不调用模型。
2. **宿主维持**：没有[信号](agent.signals.md)触发、没有新的用户输入、[复查条件](agent.review_when.md)都没满足，系统规则维持，不调用模型。
3. **预算闸门**：批处理开始 1500 秒后（`stage_budget`）、Agent 不可用、已遇额度用尽（`quota_exhausted`）、当日 token 上限（`token_budget`），写一条跳过记录；同一条链当天已有 3 条跳过或出错记录后不再写行，只在阶段跳过里记 `max_attempts`。
4. **单阶段复查**：调用一次 `agent-review-1`，工具按复查交易日取数。输出无效或模型出错记为出错。维持可以不引用，修订和作废必须引用（`invalid_citation`），结论格式不对为 `review_malformed`。
5. **维持或作废**：照写一条记录。
6. **修订**：再走完整的提议、反方、校验；通过则新计划行、新版本行、复查记录在同一个事务里写入，成为新的最新版。校验不过，或入场期新版按规则作废（如有效期内有财报，`plan_invalid`），则记一条"修订"记录但没有新版，最新版不变，该阶段记为降级。修订时 Agent 改口为不买（`pass`），按作废处理。

**复查对象**：入场期（`entry`）和持仓期（`holding`）的链。当天刚生成的版本不复查（`fresh_version`），当天已有有效批处理复查的不再复查（`already_reviewed`）。顺序是持仓期在前，其次主列表入场期，扩展池最后；一次最多 20 条，超出记 `object_cap`。

## 在系统里哪里看到
计划页"复查记录"（交易日、触发方式、结论、判定方、第几版到第几版、理由、各条件判定）；「今日」"进行中的 Agent 计划"里每条链的最近复查；推送的"计划更新"只推作废和产生新版的修订。

## 注意
"系统规则维持"只代表没有触发任何机械条件，不是 Agent 重新确认过。持仓期的新版即使按仓位规则不能加仓（如 `no_room`）也会成为最新版，链照常复查。

这条口径同样**未经验证**（Phase 0 Gate HOLD，见[证据状态「未验证」](system.gate_hold.md)）：它只说明系统怎么运作，不说明复查结论有效。
