---
id: system.daily_workflow
title: "每日工作流"
group: system
version: 3
applies_to: [daily-3, stages-6, daily-2, stages-5, daily-1, stages-4]
summary: "daily-3 串起十三节点并冻结本次功能开关；复查后先组合检查再账户决策，复盘后按提案日起草优化建议，续跑沿用原运行事实。"
related: [system.stages, system.stage_status, account.paper, account.portfolio_check, agent.optimize_proposal, system.feature_flags, system.push, system.fetch_policy, system.cutoff, system.append_only]
sources: [investment_assistant/core/stages.py, investment_assistant/core/features.py, investment_assistant/workflow/graph.py, investment_assistant/workflow/check.py, docs/specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md]
---

## 是什么
定时任务 `daily` 北京时间周二至周六 11:30 跑一次。`daily-3` / `stages-6` 的[十三节点](system.stages.md)顺序为 `sec_monitor → collect → screen → regime → signals → plan → agent → review → portfolio → accounts → push → retro → propose`。历史 daily-2/stages-5 是十一节点，daily-1/stages-4 是十节点，按原版本读取。

## 怎么算 / 从哪来
- **检查点**：每个节点结束时，整条链的状态（池、持仓、各节点的产出引用、运行摘要）同步存进数据库。运行编号 `daily-…` 就是检查点的线程号。
- **开关冻结**：initial_state 读当前四个开关，冻结进 features 与运行摘要；续跑不重读。`portfolio_manager=false` 时整个 portfolio 节点 `skipped/feature_disabled`，`[fact:portfolio_check]` 写「本版未检查」，`validate_orders` 收到 `None` 不作组合拦截，Today 为 `not_computed`，accounts 照常；`optimize_proposals` 关闭不影响已有提案的决定与 brief。
- **新节点边界**：`portfolio_manager=true` 时 portfolio 先存各账户硬结果，再尝试真实账户 PM；硬结果已成功产出且后续 PM 模型失败时，保留硬结果并显示 `degraded`，不能与关闭功能混为一谈。propose 在 retro 后仅提案日执行，批准前不创建路线版本或 brief。研究请求只产档案、不开链。
- **两层重试**：每个外部请求按目标重试（见[外部请求策略](system.fetch_policy.md)）；节点本身抛出没人接住的异常时，等 60 秒自动重试一次，第一次的错误记在这一行的 `detail.retry`。数据库连不上（`Unavailable`）不等、直接按失败处理。
- **停在失败的节点**：重试后仍失败，该节点记 `failed`，它后面的节点各记一行 `skipped`、原因 `upstream_failed`，运行状态是失败，并发一条「每日工作流失败」通知（每次执行至多一条）。其中市场环境 `regime` 出错也会停链，因为信号依赖当天的区制。
- **续跑**：只有停了链的失败才能续跑（上面“停在失败的节点”）。在运行详情点「从此节点继续」，从检查点接着跑。节点自己记了 `failed`、链却照常跑完的失败（例如推送没发出去）没有下一个节点可续，只能整次重新运行，失败通知里也没有“续跑”一行。已完成的节点不重跑；交易日（session）沿用原运行，普通节点仍用原运行的[截止时间](system.cutoff.md)，采集类节点丢掉来源时间晚于截止时间的资讯和申报；下一个交易日纽约 17:00 之后不再允许续跑，只能整次重新运行。
- **账户节点的截止时间**：`accounts` 每次节点尝试开始时单独读取一次当前墙钟，冻结本次账户截止时间，session 不变；本轮上游在此时之前已提交的计划可用，之后的版本仍被排除。先复用本账户同 session 已有的决策与原 `cutoff_at`；尚无决策的账户才使用新尝试的截止时间。阶段与新决策的 `detail.cutoff_at`、有证据时的 `account.cutoff_at` 显式记录所用时点，决策 API 回显原决策时点；各项数据仍按对应截止时间过滤。
- **`attempt`**：续跑只给重新执行的节点（失败节点和它后面记了 `upstream_failed` 的节点）追加一行 `attempt = n+1`；失败行与跳过行是真实发生过的，不会被改写（[只追加](system.append_only.md)）。「十三行」读作每个节点的有效行（最大 `attempt`）共十三条；已完成节点不追加新的执行行。
- **运行状态**：由有效行推出：有 `failed` 为失败，否则有 `partial` 或 `degraded` 为降级，否则成功；跳过（休市、`manual_run`、`not_due`）不影响。降级不发通知。
- **并发**：同一时刻只允许一次执行（定时、手动、续跑、SEC 回补共用一把数据库锁）；后到的手动运行得到「正在运行」，定时运行记一行 `daily_running`。
- **预算**：agent + review 共用1500秒，含研究请求与补查；portfolio 的 PM 独立300秒；accounts 共用600秒；retro 起草1500秒；propose 600秒；批外手动研究900秒。全部 token 计每日总量，上游采集重试不占这些窗口。daily 忙时研究请求成功入队、按 FIFO 处理；已开始的研究与后来启动的 daily 不另加互斥，共享族锁与 token 总量。

## 在系统里哪里看到
「工具 → 运行记录」每一行的「详情」打开运行详情页：节点状态图、每个节点做了什么、拉取详情和结果详情；失败的节点上有「从此节点继续」。命令行：`python -m investment_assistant.tasks.daily show --run-id …`。

## 注意
历史 daily-1 依赖迁移028，daily-2 再依赖029/030；daily-3 还要求031，缺失时以 `migration_031_missing` 拒跑，新接口返回503。手动运行不推送，周 / 月只生成不推送。编排、来源完整与数值一致不表示策略有效，Gate HOLD 保持。
