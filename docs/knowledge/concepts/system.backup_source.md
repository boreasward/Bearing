---
id: system.backup_source
title: 主源与备源
group: system
version: 1
applies_to: [fetch-1]
summary: "主源最终失败后仅对该目标尝试一次已启用备源；财报日启用 Nasdaq，Stooq 仅保留解析和补缺机制，日线正式仍只有 yfinance。"
related: [system.fetch_policy, system.adapter_registry, system.feature_flags, family.bars, family.earnings]
sources: [investment_assistant/data/registry.py, investment_assistant/data/adapters/bars.py, investment_assistant/data/adapters/earnings.py, investment_assistant/data/sources/stooq.py, investment_assistant/data/sources/nasdaq.py]
---

## 是什么

备源是主源取某个目标失败后的有限补救，不是两个来源混着取平均。当前正式日线只登记 yfinance；Stooq 解析器与补缺机制已存在但**未启用，不是可用备源**。财报日的 Nasdaq 是实际启用备源。

## 怎么算 / 从哪来

- 仅主源最终 `timeout`、`network`、`http_5xx`、`http_429`、`empty`，且存在第二源、`backup_sources` 开启时切换；`http_4xx`、`parse`、`other` 不切。
- 备源只试一次（空退避列表）。已有 SEC/FRED/earnings 内部重试不再外套同一重试；attempts 按真实请求合计。落库失败不再切源。
- 两源都失败仍保留主源错误种类、状态码与原因；`fallback.reason` 也记主源原因，`source_used` 记录产生所尝试存储数据的源，不能把这个字段当成功标志。
- 未启用的日线补缺机制限定最近10个交易日，只补没有任何 revision 的日期；不覆盖已有日期。Stooq 行的 `adj_close = close × r`，r 取该标的主源在缺失日及之前最近一行的比例，无主源为1。主源恢复且值不同时追加 revision，读取取最新版本；这些机制已测不等于 Stooq 已可用。
- 其余数据族无备源，失败照实显示；关闭开关后 `fallback.tried=false`，不隐瞒主源缺口。

## 在系统里哪里看到

数据卡的「今日来源」区分主源、备源与失败目标；目标明细显示 `source_used`、`fallback` 与 attempts；原始日线和财报日保留 source。

## 注意

沿用复权比例只是补缺机制的口径，不保证消除除息误差。源失败、空结果与旧值要区分，成功取数也不使策略脱离 Gate HOLD。
