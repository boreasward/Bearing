---
id: system.daily_workflow
title: "每日工作流"
group: system
version: 2
applies_to: [daily-2, stages-5, daily-1, stages-4]
summary: "daily-2 把十一个节点串成一条链，每节点结束落检查点，出错 60 秒后重试一次，再失败停链；账户结算在每日复查后、推送前。"
related: [system.stages, system.stage_status, account.paper, system.push, system.fetch_policy, system.cutoff, system.append_only]
sources: [investment_assistant/workbench/workflow.py, investment_assistant/tasks/daily.py, investment_assistant/workbench/service.py, investment_assistant/workbench/paper_accounts.py]
---

## 是什么
定时任务 `daily` 北京时间周二至周六 11:30 跑一次。`daily-2` 按固定顺序走[十一个节点](system.stages.md)：`sec_monitor`、`collect`、`screen`、`regime`、`signals`、`plan`、`agent`、`review`、`accounts`、`push`、`retro`。每个节点写一行运行证据；[账户与模拟交易](account.paper.md)在每日复查之后结算和决策，再进入推送。历史 `daily-1` / `stages-4` 没有 `accounts`，仍按十个节点读取。

## 怎么算 / 从哪来
- **检查点**：每个节点结束时，整条链的状态（池、持仓、各节点的产出引用、运行摘要）同步存进数据库。运行编号 `daily-…` 就是检查点的线程号。
- **两层重试**：每个外部请求按目标重试（见[外部请求策略](system.fetch_policy.md)）；节点本身抛出没人接住的异常时，等 60 秒自动重试一次，第一次的错误记在这一行的 `detail.retry`。数据库连不上（`Unavailable`）不等、直接按失败处理。
- **停在失败的节点**：重试后仍失败，该节点记 `failed`，它后面的节点各记一行 `skipped`、原因 `upstream_failed`，运行状态是失败，并发一条「每日工作流失败」通知（每次执行至多一条）。其中市场环境 `regime` 出错也会停链，因为信号依赖当天的区制。
- **续跑**：只有停了链的失败才能续跑（上面“停在失败的节点”）。在运行详情点「从此节点继续」，从检查点接着跑。节点自己记了 `failed`、链却照常跑完的失败（例如推送没发出去）没有下一个节点可续，只能整次重新运行，失败通知里也没有“续跑”一行。已完成的节点不重跑；交易日（session）沿用原运行，普通节点仍用原运行的[截止时间](system.cutoff.md)，采集类节点丢掉来源时间晚于截止时间的资讯和申报；下一个交易日纽约 17:00 之后不再允许续跑，只能整次重新运行。
- **账户节点的截止时间**：`accounts` 每次节点尝试开始时单独读取一次当前墙钟，冻结本次账户截止时间，session 不变；本轮上游在此时之前已提交的计划可用，之后的版本仍被排除。先复用本账户同 session 已有的决策与原 `cutoff_at`；尚无决策的账户才使用新尝试的截止时间。阶段与新决策的 `detail.cutoff_at`、有证据时的 `account.cutoff_at` 显式记录所用时点，决策 API 回显原决策时点；各项数据仍按对应截止时间过滤。
- **`attempt`**：续跑只给重新执行的节点（失败节点和它后面记了 `upstream_failed` 的节点）追加一行 `attempt = n+1`；失败行与跳过行是真实发生过的，不会被改写（[只追加](system.append_only.md)）。「十一行」读作每个节点的有效行（最大 `attempt`）共十一条；已完成的节点只有 `attempt 1`，这就是它没有重跑的证明。
- **运行状态**：由有效行推出：有 `failed` 为失败，否则有 `partial` 或 `degraded` 为降级，否则成功；跳过（休市、`manual_run`、`not_due`）不影响。降级不发通知。
- **并发**：同一时刻只允许一次执行（定时、手动、续跑、SEC 回补共用一把数据库锁）；后到的手动运行得到「正在运行」，定时运行记一行 `daily_running`。
- **预算**：Agent 与每日复查共用一个 1500 秒窗口，从 `agent` 节点开始起算；账户组合决策在 `accounts` 节点开始起算独立 600 秒窗口，所有账户共用；复盘卡起草在 `retro` 节点里有自己的 1500 秒。这些 Agent 调用仍共用同一个每日 token 上限，上游采集的重试和等待不占这些窗口。

## 在系统里哪里看到
「工具 → 运行记录」每一行的「详情」打开运行详情页：节点状态图、每个节点做了什么、拉取详情和结果详情；失败的节点上有「从此节点继续」。命令行：`python -m investment_assistant.tasks.daily show --run-id …`。

## 注意
历史 `daily-1` 依赖迁移 028；当前 `daily-2` 在此基础上还要求迁移 029 的可执行计划与迁移 030 的账户结构。缺失时分别记 `migration_028_missing`、`migration_029_missing`、`migration_030_missing` 并拒绝运行。手动运行（「立即运行批处理」）不推送，周 / 月复盘只生成不推送。
