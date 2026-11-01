---
id: review.weekly
title: "周度复盘"
group: review
version: 3
applies_to: [review-6, review-7, review-8]
summary: "每周冻结一份快照：按规则、按执行、执行偏差、计划外成交、计划影子、纪律、分池多个视角统计，并冻结当期三方对比；更正要重新生成。"
related: [review.control_group, review.tracks, review.first_latest, review.monthly_return, system.push, review.weekly_learning]
sources: [investment_assistant/review/outcomes.py, investment_assistant/review/period_digest.py, investment_assistant/tasks/daily.py]
---

## 是什么
周度复盘把一周内信号日落在该周的单元与结果汇总成一份不可变的快照，让你每周对照自己的纪律与系统的表现。

## 怎么算 / 从哪来
快照包含这些视角：

- **按规则 / 按执行**（`review-8`，v2.9）：对照组的超额收益，按规则、按执行状态（已采纳、有效期外、未执行、计划外）分组，见[对照组](review.control_group.md)与[执行状态与备注](review.execution_status.md)；`units` 记已执行 / 未执行的单元数。计划外一组只有计数（没有信号可回放）。`review-7` 及更早是按决定（采纳、否决、降级），页面照旧显示。
- **执行偏差**：成交价相对信号后下一交易日开盘价的偏离，以及从**所关联的计划版本生成**到成交的延迟小时数（`review-7` 及更早是从决定到成交）。
- **计划外成交**（`unplanned_trades`，`review-8`）：本周没有关联任何计划的买入。`review-7` 及更早叫「无信号交易」。
- **已平仓交易**：持有交易日数、盈亏、同期 SPY、最大浮盈与浮亏。
- **计划影子**：按计划种类汇总，Agent 分首版与最新版两个口径，见[首版与最新版汇总](review.first_latest.md)。`review-8` 起 `plans` 列出全部计划：带执行字段的按 `plan-shadow-2` 用原来的键，旧计划按 `plan-shadow-1` 用带口径名的键（如「规则（旧计划 · plan-shadow-1）」），两种口径各自汇总、不混算；`plans_counterfactual` 是前者在 `plan-shadow-1` 下的反事实。
- **纪律**：追高进场、过早止盈、死扛亏损、计划外交易四项与遵守率（`review-8` 起是 `discipline-2`）。
- **分池**：核心（`core`）、筛选（`screen`）、扩展（`extended`）三类。
- **三方对比**：当期冻结一份（`review-8` 起是 `tracks-2`）。
- **你学到了什么**（`review-7` 起）：本周结束的交易的纪律四项、本周的复盘卡摘要、本周被检验的知识，见[你学到了什么](review.weekly_learning.md)；`review-6` 及更早的快照没有这一段，不重算。

**冻结与更正**：快照生成后不再改动；要更正就对同一周重新生成，新快照指向旧快照。**推送**：每日工作流的 `retro` 节点在北京时间周二至周六 11:30 检查一次，每周最后一个交易日收盘后的首次运行会冻结并推送该周快照（手动运行只生成不推送）。

## 在系统里哪里看到
「复盘 → 周度复盘」，卡片上有"推送到 Discord"按钮；推送里多一个"你学到了什么"字段。

## 注意
所有数字是描述性统计，样本小且互相重叠，快照里也写明"不代表规则有效"。
