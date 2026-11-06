---
id: system.roadmap_records
title: 系统内路线记录
group: system
version: 1
applies_to: [agent-optimize-1, agent-impl-brief-1, daily-3]
summary: "目标、版本、决策和证据在系统内只追加修订；仓库 roadmap 是导出件，页面历史只读真实 revision，改路线先走提案决定。"
related: [agent.optimize_proposal, agent.implementation_brief, system.append_only, system.feature_flags]
sources: [investment_assistant/optimize/repo.py, investment_assistant/optimize/export.py, investment_assistant/optimize/roadmap.py, investment_assistant/api/routes/roadmap.py, docs/specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md]
---

## 是什么

路线的事实来源是系统内 `roadmap_goals/roadmap_versions/roadmap_decisions/roadmap_evidence`；每条记录有逻辑键与从1开始的 revision，新行 supersedes 旧行。仓库 `roadmap.md` 是导出件，改路线走[提案](agent.optimize_proposal.md)。

## 怎么算 / 从哪来

- 版本状态为 `planned/in_progress/engineering_done/user_acceptance_pending/done/dropped`；工程完成与用户验收完成分开，不能把批准 planned 当作交付。
- 初始 YAML 种子由人工整理、CLI 幂等导入；同键同内容跳过，不同内容报错。导出按目标、版本总览、各版定义、决策、证据索引、未决提案呈现，用户原话逐字保留。
- 版本详情是 `{version,history,brief,brief_status}`；version 是最新行，history 是全部真实 revision、按 version/id 升序且包含当前行。决策详情同理返回 `{decision,history}`；不存在返回404，不拼造历史。
- 最新 brief 可空，状态 `not_requested/pending/ok/error`；读取该状态与历史，不把后台任务已提交显示成 brief 已成功。
- `get_roadmap` 只向 optimize、brief、discuss 提供截止前最新记录，引用带 `roadmap:<表>:<id>@<version>`；历史阅读不把今日重算资料装进旧 revision。

## 在系统里哪里看到

Roadmap 页四个页签：路线、提案、决策、证据；版本/决策详情可展开只读修订，版本详情可看实施 brief，导出返回同一份 Markdown。

## 注意

记录是治理与追溯依据，不是运行时 prompt 热更新。提案批准前无版本或 brief 引用，批准后也只是 planned；策略仍 Gate HOLD。
