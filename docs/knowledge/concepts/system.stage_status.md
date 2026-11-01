---
id: system.stage_status
title: "阶段状态"
group: system
version: 2
applies_to: [stages-4, stages-3, stages-2]
summary: "每个阶段的状态五选一：success、partial、degraded、failed、skipped，优先级 skipped > failed > partial > degraded > success。"
related: [system.stages, agent.reject_reasons, system.push]
sources: [investment_assistant/core/stages.py]
---

## 是什么
状态是一个阶段"这次跑得怎么样"的一个词，用来让你一眼看出该不该点开细看。**部分成功和降级不会被合并成"成功"**，页面上分别显示。

## 怎么算 / 从哪来
按下面的优先级取第一个成立的（从高到低）：

| 状态 | 含义 |
| --- | --- |
| `skipped` | 阶段没有执行。常见原因：`not_trading_day`（纽约休市）、`explicit_pool`（只对指定的标的运行）、`manual_run`（手动运行不推送）、`already_pushed`、`task_disabled`、`agent_unavailable`（未连接 ChatGPT 或关闭）、`migration_026_missing`、`not_reached`；v2.8 起还有 `upstream_failed`（上游节点失败，未执行）和 `not_due`（今天没有到期的复盘） |
| `failed` | 阶段执行了但整体失败，原因形如 `aborted: 异常摘要` |
| `partial` | 有逐目标失败：一部分标的成功，一部分失败 |
| `degraded` | 没有目标失败，但有降级，例如 Agent 阶段不可用时退回规则版、修订未通过校验（`revision_failed`）、推导核对发现不一致 |
| `success` | 以上都没有 |

**有效行**：续跑后同一个节点可以有几行（`attempt` 1、2…），状态以最大 `attempt` 的那一行为准；整次运行的状态由十个有效行推出：有 `failed` 为失败，否则有 `partial` 或 `degraded` 为降级，否则成功（跳过不影响）。

每个阶段的行里还有：输入与产出计数、按原因分组的跳过（每个原因最多留 10 个样本）、逐目标失败的原因、降级列表、产出引用（运行、候选、计划、复查记录的 id）。

## 在系统里哪里看到
运行一览每一行的状态徽标与"跳过 / 失败 / 降级"逐条原因。

## 注意
没有证据时页面会明说没有，不会显示成"成功"。"跳过"不等于出错：很多是预期之内的（如休市或手动运行）。
