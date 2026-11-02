---
id: agent.optimize_proposal
title: 系统优化提案
group: agent
version: 1
applies_to: [agent-optimize-1, daily-3, stages-6]
summary: "周六运行由冻结证据包起草至多3条提案；引用逐行校验，批准前不写版本或 brief，更不自动修改代码、prompt 或策略。"
related: [system.roadmap_records, agent.implementation_brief, system.feature_flags, system.gate_hold]
sources: [investment_assistant/optimize/evidence.py, investment_assistant/optimize/proposal_check.py, investment_assistant/optimize/repo.py, investment_assistant/optimize/proposals.py, investment_assistant/workflow/nodes/propose.py, docs/specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md]
---

## 是什么

`propose` 在 retro 之后，仅北京时间周六的运行执行（对应纽约周五 session）；其它日 `not_proposal_day`，开关关闭 `feature_disabled`。`agent-optimize-1` 只有建议权，没有实施权。

## 怎么算 / 从哪来

- 宿主先读固定时间窗并冻结五段证据：`data_failures`（近7天逐族错误/备源/部分成功）、`agent_quality`（近7天模式与原因、研究缺口、补查延期）、`retro_patterns`（近30天同 scope 教训重复至少2次）、`route`（待用户验收超过30天及既有主指标最近3个月信号）、`open_proposals`（未决提案）。没有观测的段为空，不伪造零；全空不调模型，记 no_evidence。
- 旧字段 `counter_adoption` 实际只观察「有反方意见的运行之后出现成功修订后继」比例，measurement 为 `counter_followed_by_successful_revision`：**运行级后续修订比例，不证明逐条反方意见被采纳或因果关系**。无分母时为空，不写成0%或采纳率。
- 至多3条合法且不重复的提案，source 为 `data/agent_quality/route`；change/rationale/expected/acceptance 分别至多500/500/200/300字。每条非空 evidence_refs 必须精确指向包内 `fact:<key>#<id>`，无效条目丢弃并记原因，不挤占合法额度。
- 工具仅 `get_roadmap/get_concept/get_knowledge`，独立600秒预算，token 共用日上限。生成时证据包保存于该运行 `agent_runs.output.evidence_pack`；列表的 `evidence_rows=[{ref,body}]` 按原 refs 从该包取，历史缺包则空并提示，绝不重新执行今日SQL冒充当时证据。
- 状态为 proposed（待决定）、approved（已批准）、rejected（已驳回）、superseded（已取代）。人工批准/驳回均需理由与 request_id；批准才同事务创建 planned 版本与决策，再后台生成 brief；驳回不产生版本或 brief。
- proposed 提案不能被任何 roadmap_versions/roadmap_briefs 引用，宿主核对该不变量。提案出现不代表系统已变更；周六 push 在本节点之前，提案提示进入下一次周度复盘推送。

## 在系统里哪里看到

Roadmap「提案」看冻结证据、预期效果、验收与决定；运行详情 propose 列出本次提案引用。批准后继续读回真实 brief 状态。

## 注意

证据描述相关观测，不证明因果效果；提案、批准与 brief 都不部署代码或 prompt，Gate HOLD 不变。
