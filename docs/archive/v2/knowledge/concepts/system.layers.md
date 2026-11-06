---
id: system.layers
title: 五层职责与依赖方向
group: system
version: 1
applies_to: [daily-3, indicator-1, portfolio-check-1, agent-4]
summary: "数据、推导、分析、执行、优化分别拥有自己的口径；import 顺序与每日运行顺序不同，共享常量和类型下沉，不复制公式或靠懒导入消环。"
related: [system.adapter_registry, indicator.registry, agent.company_researcher, account.portfolio_check, agent.optimize_proposal, system.daily_workflow]
sources: [investment_assistant/core/versions.py, investment_assistant/data/registry.py, investment_assistant/derivation/indicators/registry.py, investment_assistant/execution/portfolio.py, investment_assistant/optimize/evidence.py, docs/specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md]
---

## 是什么

「五层」说明业务职责：数据取存事实、推导计算指标、分析形成判断、执行维护计划/账户约束、优化提出改进。实际包另有 core 共享底座、review 复盘、workflow 编排与 views 只读投影。

## 怎么算 / 从哪来

依赖方向固定为 `core ← data ← derivation ← execution ← analysis ← review ← optimize ← workflow / views ← api / services / tasks / notify`；左侧不能 import 右侧。db、config、runtime_paths 是更低的底座。

- execution 拥有计划、链、账本、模拟成交与组合检查纯函数；analysis 使用它们检查 Agent 输出，不复制公式。
- workflow 决定每日「先分析、再执行」的顺序；import 上 execution 在 analysis 之下，两种顺序不能混淆。跨层只读页面放 views。
- 版本、文案、事务、锁、任务底座、共享类型下沉到唯一所有者；循环依赖用下沉/拆文件解决，不用函数内懒 import、动态 import 或兼容旧包来掩盖。
- 搬家不改变既有函数、数值和历史口径；新增研究运行是 agent-4，计划 schema 仍 agent-3，注册指标也保留原算法。独立推导重算继续独立。

## 在系统里哪里看到

原理页按运行顺序讲系统；概念卡 sources 指向实际负责口径的新分层模块或冻结规范。阶段、账户与指标版本各自标识其职责，不拿发布版本冒充计算版本。

## 注意

结构分层是可维护性约束，不是策略有效性证据；资料完整、依赖无环、数值一致仍不能解除 Gate HOLD。
