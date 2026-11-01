---
id: agent.changes
title: "相对上版变化"
group: agent
version: 1
applies_to: [agent-2]
summary: "修订版必须写明和上一版相比变了什么：模型的说明 changes 与系统算出的差异 diff 并列显示，字段集合必须相等。"
related: [agent.version_chain, agent.review_outcome, agent.reject_reasons, agent.citation]
sources: [investment_assistant/execution/plan_check.py, investment_assistant/execution/timeline.py]
---

## 是什么
每日复查如果认为计划需要修订，会走一遍完整的提议、反方和校验，产生新的一版。新版必须说清"相对上一版改了什么"，这有两个来源并列显示：

- **宿主差异 `diff`**：系统自己比较新旧两版，逐字段列出前后值。可比较的字段共七个：`action`、`entry`、`stop`、`target1`、`thesis`、`invalidation`、`review_when`。价格按分显示，条件按 `kind` 与参数比较，不比较条件文案和引用。
- **模型的变化说明 `changes`**：每条是"哪个字段、怎么说、依据什么引用"。

## 怎么算 / 从哪来
- **首版不填**：第一版没有"上一版"，填了 `changes` 会作废（`malformed`）。
- **修订版必填**：没写或写成空，作废（`changes_mismatch`）；格式不对为 `malformed`；说明里的引用不存在为 `invalid_citation`。
- **字段集合必须相等**：模型说明涉及的字段，必须恰好等于系统算出的差异字段，多一个少一个都作废（`changes_mismatch`）。
- **没有任何差异**：新版和上一版完全一样，作废（`no_change`）。

## 在系统里哪里看到
修订版的 Agent 计划块里"相对第 n−1 版"：宿主差异（如"止损 a → b"）与模型的说明并列。推送的"计划更新"里，修订条目的变化说明取自这里的 `changes`。

## 注意
并列显示是为了让你能核对模型"说的"和系统"算的"是否一致；字段集合相等只保证没有遗漏或虚报，不保证说明的理由正确。

这条口径同样**未经验证**（Phase 0 Gate HOLD，见[证据状态「未验证」](system.gate_hold.md)）：它只说明系统怎么运作，不说明修订有效。
