---
id: system.stages
title: "每日十一个节点"
group: system
version: 4
applies_to: [stages-5, stages-4, stages-3, stages-2, stages-1]
summary: "daily 按固定顺序跑十一个节点，每个节点各写运行证据；stages-5 在每日复查后、推送前加入 accounts，结算账户并作模拟决策。"
related: [system.daily_workflow, system.stage_status, system.screen, agent.review_outcome, account.paper, system.push, review.retro_card]
sources: [investment_assistant/core/stages.py, investment_assistant/workflow/graph.py, investment_assistant/tasks/daily.py, docs/operations/scheduling-and-notifications.md]
---

## 是什么
定时任务 `daily`（北京时间周二至周六 11:30，见[每日工作流](system.daily_workflow.md)）对最近收盘的交易日跑一遍完整流程。`stages-5` 把流程拆成十一个固定顺序的节点，每个节点结束时各写一行运行证据，你在「运行一览」和运行详情里看到的就是这些有效行。

## 怎么算 / 从哪来
| 顺序 | 阶段 id | 名称 | 做什么 |
| --- | --- | --- | --- |
| 1 | `sec_monitor` | SEC 监控 | 扫描观察名单的 SEC 申报，归档原文、提取正文事实并经 Discord Bot 直发通知；已通知过的不重发 |
| 2 | `collect` | 数据采集 | 日线、资讯标题、财报日、SEC 列表与 FRED 宏观，加纳指 100 范围成分的日线与财报日，是后面各节点的数据底座 |
| 3 | `screen` | 筛选 `screen-1` | 读库在纳指 100 范围内按 `screen-1` 过滤排序，入选成员并入扩展观察池，并为新成员补采资讯与 SEC |
| 4 | `regime` | 市场环境 `regime-2` | 用范围加手动池的广度与宏观序列判定区制，并计算行业强弱 |
| 5 | `signals` | 信号与候选 | 规则 v1 在观察池与持仓上逐只评估，产生候选并合成每股每日一个决策单元 |
| 6 | `plan` | 规则计划 `plan-1` | 为无冲突、未决定的买入单元生成计划：ATR 止损、2R 目标、按风险定仓、超限作废 |
| 7 | `agent` | Agent 建议 | Agent 对候选做提议与反方论证并给出解读；不可用时整节点跳过 |
| 8 | `review` | 每日复查 | 对每份有效 Agent 计划复查一次：维持、修订或作废 |
| 9 | `accounts` | 账户 | 结算模拟挂单与退出，写各账户权益与持仓状态，再作下一交易日的[模拟决策](account.paper.md) |
| 10 | `push` | 复核与推送 | 组装今日视图并把摘要推送 Discord；手动运行不推送 |
| 11 | `retro` | 复盘 | 交易日检测真实账户已结束的计划与平仓并起草[复盘卡](review.retro_card.md)；周 / 月复盘到期时生成并推送 |

每次运行向 `stage_runs` 追加十一个节点的有效行（`stages-5`）；续跑时只给重新执行的节点追加 `attempt` 更大的行。每行记状态（见[阶段状态](system.stage_status.md)）、输入与产出计数、按原因分组的跳过、逐目标失败、降级、产出引用和耗时；耗时是节点真实起止之差，含重试等待。

**`stages-4`**：v2.8 到 v2.10 的版本有十个节点，没有 `accounts`；历史运行仍按当时的节点集合展示。

**`stages-3`**：v2.7 的版本有九个阶段，没有 `sec_monitor`，复盘卡 `retro` 在 `push` 之前。

**`stages-2`**：v2.5 到 v2.6 的版本有八个阶段，没有 `retro`。任务开始时如果库里还没有迁移 027，本次就按 `stages-2` 记 8 行，运行报告的 `errors` 里有一条 `growth: migration_027_missing`。

**`stages-1`**：迁移 026 之前的版本只有七个阶段，没有 `review`。任务开始时如果库里还没有迁移 026，本次就按 `stages-1` 记 7 行。

纽约休市日，`push` 之前的九个节点整体跳过，原因 `not_trading_day`，`push` 只发一行休市提示，`retro` 仍判断周 / 月复盘是否到期（都没到期记 `not_due`）。手动运行针对最近一个已收盘的交易日，总是不推送。

## 在系统里哪里看到
「今日」顶部的运行一览（每个节点一句话加计数和跳过原因，可选日期看往日）；运行详情页（节点状态图、拉取详情、续跑）；「原理」页每一步的"当天这一步"。

## 注意
证据写入失败只记在运行报告里，不影响运行本身。某个节点重试后仍出错时，它记为失败（`aborted`），后面的节点记 `skipped / upstream_failed`，仍是完整的十一行；从失败的节点续跑后，有效行恢复为十一条正常的行。
