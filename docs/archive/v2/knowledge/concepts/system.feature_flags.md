---
id: system.feature_flags
title: 功能开关与运行冻结
group: system
version: 1
applies_to: [daily-3, agent-4, agent-portfolio-2, agent-optimize-1]
summary: "四个默认开启的熔断开关只追加翻动记录；daily 开始时冻结、续跑沿用。关闭 portfolio_manager 跳过整个组合节点，今日显示未计算。"
related: [system.daily_workflow, system.backup_source, agent.company_researcher, account.portfolio_check, agent.optimize_proposal, system.roadmap_records]
sources: [investment_assistant/core/features.py, investment_assistant/api/routes/features.py, docs/specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md]
---

## 是什么

四个开关是问题模块的熔断阀，不是分阶段发布配置；没有变更记录时默认全部开启。开和关都必填理由，关闭还需确认，`request_id` 保证幂等。

## 怎么算 / 从哪来

| 开关 | 关闭时 |
| --- | --- |
| `backup_sources` | 只尝试主源，fallback.tried=false，失败照实记 |
| `company_researcher` | 首版研究段 disabled，提议者仍为 agent-4；新研究请求返回 feature_disabled |
| `portfolio_manager` | 整个 portfolio 节点 skipped/feature_disabled；[fact:portfolio_check] 写「本版未检查」；validate_orders 收到 None，不作组合拦截；Today not_computed |
| `optimize_proposals` | propose 跳过；路线阅读、决定、brief 与导出仍可用 |

当前值由 `feature_flag_changes` 最新真实行决定；每行保存 enabled_before、enabled_after、reason、request_id、created_at。daily 初始状态冻结 features，并记入运行报告，续跑沿用；运行中翻动到下一次 daily 才生效，批外入口读取当下值。

`portfolio_manager=false` 是关闭整个组合功能，不是仅关闭 PM 意见。只有 `portfolio_manager=true`、硬结果已成功产出而后续 PM 模型失败时，才保留硬结果并显示 `degraded`；关闭路径是 `not_computed`。原有现金、股数等订单校验仍继续执行。

`GET /api/settings/features` 同时返回 flags 与 history；history 是全部真实变更，按 id 升序，不能由当前值拼出。POST 成功后读回两者，空历史就显示尚无变更。

## 在系统里哪里看到

「设置 → 功能开关」看当前状态与历史；账户权益/对比图标 feature、version、params 三类分界，分别来自开关变更、决策运行版本首次出现和参数生效日。

## 注意

开关关闭不是删除旧证据或改写旧运行；纯硬检查不等于模型意见。分界只用于识别口径变化，不据此归因收益，所有结果仍 Gate HOLD。
