---
id: family.universe
title: 纳指 100 范围
group: family
version: 1
applies_to: [screen-1]
summary: "手动维护的纳指 100 成分名单（版本化文件），是每日筛选的范围和区制广度样本；as_of 超过 120 天记 universe_stale。"
related: [system.screen, indicator.breadth, system.pool_tiers]
sources: [investment_assistant/data/adapters/universe.py, config/universes/nasdaq100.json, docs/archive/v2/features/pool-and-context.md, docs/archive/v2/features/data-menu.md]
---

## 是什么

纳指 100 范围是一份放在仓库里的成分名单，供两件事使用：每日筛选在这份名单里挑当天值得关注的股票，区制广度把它当作样本池。它**不是**自动同步的数据，而是手动维护、随版本走的文件。

## 怎么算 / 从哪来

- 文件里有名称、`as_of`（名单对应的日期）、来源、备注和成员列表；GOOG 与 GOOGL 只保留 GOOGL。
- 校验规则：成员代码都必须合法，去重后须在 90–105 只之间，否则文件被视为不可用。
- `as_of` 距批处理日超过 120 天，运行报告记 `universe_stale`，筛选照常运行，但提醒你该更新名单了。
- 已在你手动观察名单里的股票不参加筛选，它们本来就每天在跑。

## 在系统里哪里看到

- 「数据」菜单的「纳指 100 范围」卡：名称、`as_of`、来源、备注与成员最新日线；文件读不出来时显示降级原因。它没有单只拉取按钮；是否过期看 `as_of`，不看最新时点。
- 「今日」的筛选卡片。

## 注意

- 名单靠人工更新，成分调整之后可能有一段时间与真实指数不一致。
- 文件加载失败时，筛选整体记为出错，只保留锁定和保留期内的成员，区制广度退回只用手动观察池。
