---
id: system.stages
title: "每日十三个节点"
group: system
version: 5
applies_to: [stages-6, stages-5, stages-4, stages-3, stages-2, stages-1]
summary: "stages-6 按固定顺序记录十三节点：复查后增加组合检查、复盘后增加优化提案；历史运行仍按原节点版本展示。"
related: [system.daily_workflow, system.stage_status, system.screen, agent.review_outcome, account.portfolio_check, account.paper, system.push, review.retro_card, agent.optimize_proposal]
sources: [investment_assistant/core/stages.py, investment_assistant/workflow/graph.py, docs/specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md]
---

## 是什么
定时任务 `daily`（北京时间周二至周六 11:30，见[每日工作流](system.daily_workflow.md)）对最近收盘的交易日跑一遍完整流程。`stages-6` / `daily-3` 共十三个节点，每个节点各写运行证据；历史记录仍按自身版本读取，不向旧运行补造两个节点。

## 怎么算 / 从哪来
| 顺序 | 阶段 id | 名称 | 做什么 |
| --- | --- | --- | --- |
| 1 | `sec_monitor` | SEC 监控 | 扫描观察名单的 SEC 申报，归档原文、提取正文事实并经 Discord Bot 直发通知；已通知过的不重发 |
| 2 | `collect` | 数据采集 | 按注册表处理九族，基本面、正文与行业纳入；每族每 scope 记录来源、结果与降级 |
| 3 | `screen` | 筛选 `screen-1` | 读库在纳指 100 范围内按 `screen-1` 过滤排序，入选成员并入扩展观察池，并为新成员补采资讯与 SEC |
| 4 | `regime` | 市场环境 `regime-2` | 用范围加手动池的广度与宏观序列判定区制，并计算行业强弱 |
| 5 | `signals` | 信号与候选 | 规则 v1 在观察池与持仓上逐只评估，产生候选并合成每股每日一个决策单元 |
| 6 | `plan` | 规则计划 `plan-2` | ATR 止损、2R 目标、按风险定仓；既有执行字段与数值保持 |
| 7 | `agent` | Agent 建议 | 首版先公司研究再提议/反方/校验；单元后依次处理研究请求和补查 |
| 8 | `review` | 每日复查 | 维持、修订或作废；修订沿用研究段，节点尾部也处理补查 |
| 9 | `portfolio` | 组合检查 | portfolio_manager=false 时整个节点 skipped/feature_disabled，Today not_computed；开启且硬结果已成功产出后，PM 失败才保留硬结果/degraded |
| 10 | `accounts` | 账户 | 结算模拟挂单与退出，写权益与持仓状态，再作下一交易日的[模拟决策](account.paper.md) |
| 11 | `push` | 复核与推送 | 摘要含组合检查；手动运行不推送 |
| 12 | `retro` | 复盘 | 检测真实账户结束事件并起草[复盘卡](review.retro_card.md)；周 / 月复盘到期时生成并推送 |
| 13 | `propose` | 优化提案 | 北京周六运行从冻结证据起草提案；批准前不写版本或 brief |

每次运行的有效行按 `stages-6` 对应十三节点；续跑仅给重新执行的节点追加更大 attempt。每行记[状态](system.stage_status.md)、计数、跳过原因、失败、降级、产出引用和真实耗时（含重试等待）。研究请求只产档案，不自动开链。

**`stages-5` / `daily-2`**：十一节点，没有 portfolio/propose，accounts 位于 review 与 push 之间。

**`stages-4`**：v2.8 到 v2.10 的版本有十个节点，没有 `accounts`；历史运行仍按当时的节点集合展示。

**`stages-3`**：v2.7 的版本有九个阶段，没有 `sec_monitor`，复盘卡 `retro` 在 `push` 之前。

**`stages-2`**：v2.5 到 v2.6 的版本有八个阶段，没有 `retro`。任务开始时如果库里还没有迁移 027，本次就按 `stages-2` 记 8 行，运行报告的 `errors` 里有一条 `growth: migration_027_missing`。

**`stages-1`**：迁移 026 之前的版本只有七个阶段，没有 `review`。任务开始时如果库里还没有迁移 026，本次就按 `stages-1` 记 7 行。

纽约休市日，push 前各交易日节点跳过（`not_trading_day`），push 只发休市提示，retro 仍判断周 / 月是否到期；propose 另受提案日与开关约束。手动运行针对最近已收盘交易日，不推送。

## 在系统里哪里看到
「今日」顶部的运行一览（每个节点一句话加计数和跳过原因，可选日期看往日）；运行详情页（节点状态图、拉取详情、续跑）；「原理」页每一步的"当天这一步"。

## 注意
一般未处理异常重试后仍失败则停链，后续记 `skipped/upstream_failed`；已被节点处理的降级按各节点规则继续。组合硬结果与 PM 意见必须分开呈现，不能把缺行当空组合成功。十三节点只是编排版本，所有计算仍为 Gate HOLD。
