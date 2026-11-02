---
id: agent.implementation_brief
title: 实施 brief
group: agent
version: 1
applies_to: [agent-impl-brief-1]
summary: "提案获批并创建 planned 版本后才后台起草实施说明；提交任务不等于生成成功，失败可重试且不撤销版本，brief 不能自动实施。"
related: [agent.optimize_proposal, system.roadmap_records, system.append_only]
sources: [investment_assistant/optimize/brief_check.py, investment_assistant/optimize/repo.py, investment_assistant/optimize/briefs.py, docs/specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md]
---

## 是什么

`agent-impl-brief-1` 把已批准提案、路线记录和相关概念整理成可交给实施者的 Markdown。它是说明，不是自动改代码、prompt 或参数的命令。

## 怎么算 / 从哪来

- 批准时先同事务写 planned 版本与决定，然后后台生成；未批准的提案不得有版本或 brief 引用。关闭 optimize_proposals 只停新提案，不阻止已有提案的决定与 brief。
- 内容包含背景与证据、改什么、建议模块与 prompt 段、边界、验收、风险；工具限 get_roadmap/get_concept/get_knowledge。宿主要求非空、原文不超过20000字，并有非空的验收小节；校验不改写原文。
- 每次结果写 `roadmap_briefs`，保存 ok/error 与原因。失败不删除已批准版本，也不把它改成已完成；重试另记生成结果。
- 版本详情返回最新 brief 与真实 brief_status：无批准提案关联为 `not_requested`，已批准尚无结果为 `pending`，最新行决定 `ok/error`。
- 重试返回 `{version_id,brief_status:'pending',run_id}`，run_id 是真实后台任务号；页面读回详情，以实际状态与正文为准，不把提交成功当成起草成功。驳回的决定响应 version_id/brief_status 都为空。

## 在系统里哪里看到

Roadmap 版本或提案详情的 brief 区：可读正文，pending 可刷新，error 可重试；版本与决策历史读取保存的 revision。

## 注意

brief 通过格式校验不等于实现正确或验收完成，更不提升策略证据等级；实施仍需独立验证，所有计算继续 Gate HOLD。
