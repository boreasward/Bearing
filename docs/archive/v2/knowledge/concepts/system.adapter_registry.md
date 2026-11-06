---
id: system.adapter_registry
title: 数据适配器注册表
group: system
version: 1
applies_to: [fetch-1, daily-3]
summary: "九个数据族用同一注册表声明来源、目标、存储、覆盖与补查能力；daily、手动和研究复用同一调度，指标另行现算。"
related: [system.backup_source, system.data_request, indicator.registry, family.earnings, family.fundamentals, family.sec_text, family.sector]
sources: [investment_assistant/data/registry.py, investment_assistant/data/adapter_types.py, investment_assistant/data/adapter_support.py, investment_assistant/data/tally.py]
---

## 是什么

注册表是数据族能力的唯一目录：`Adapter` 描述目标、主备 `Source`、存储回执、最新时点、滞后口径、补查工具与族锁。注册不代表源一定可用，来源状态见[备源](system.backup_source.md)。

## 怎么算 / 从哪来

- 固定顺序：`bars`、`news`、`sec_filings`、`earnings`、`fred`、`universe`、`fundamentals`、`sec_text`、`sector`。ATR 不再占一个族，归[指标注册表](../../../../knowledge/concepts/indicator.registry.md)。
- `run_adapter` 逐目标取数、存储、写 Tally；daily、筛选新成员、手动、研究、补查和 SEC 监控复用它。宿主传入冻结的 features、事务、fetcher 和配置，按 `Adapter.lock` 沿用族锁。
- `targets(conn, scope, *, explicit, facts, universe_loader)` 统一集合：daily 的池、持仓、模拟持仓/挂单、活跃链由宿主既有算法提供 `TargetFacts`；数据层不另写一套池规则。bars 是宽集合，earnings/sector 去掉 SPY 与行业 ETF；news/sec_filings/fundamentals/sec_text 取池、真实持仓、活跃链、活跃研究请求。FRED 与范围文件是全局目标。
- manual/research/screen_new_members/data_request/sec_monitor 用显式目标；空列表仍为空，不能扩大成全池。必要事实或范围加载失败要报明原因，不能吞成空成功。
- 每族每 scope 一行拉取证据；`source_used` 为源 id，`fallback={tried, reason}`，attempts 合并真实请求次数。抓取成功但落库失败仍是目标失败。universe 仅读文件，新增行数为0。

## 在系统里哪里看到

「数据」由注册表生成九族卡、覆盖与浏览入口；拉取详情显示 scope、逐目标结果、来源及主备尝试。指标组单列。

## 注意

注册表统一接线，不改变指标数值、数据截止规则或 Gate HOLD。新增族还需真实存储与迁移约束，不能只登记名字冒充完成。
