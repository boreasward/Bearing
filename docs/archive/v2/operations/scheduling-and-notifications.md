# 调度与通知

## v2.12–v2.15 合并实施运行口径（随 v2.15.0 发布，待使用验收）

新运行为 daily-3 / stages-6（13 节点），本页下方 daily-2 / stages-5 的 11 节点说明保留给旧版本运行。031 真实迁移、导入、重启与使用验收尚待单独授权；以下是操作和核对说明，不是执行记录。

- 节点顺序：sec_monitor → collect → screen → regime → signals → plan → agent → review → portfolio → accounts → push → retro → propose。每节点取最大 attempt；旧版本仍按旧列表显示，不回填新节点。
- propose 按运行 cutoff 的**北京日期是周六**判定（默认周二至周六 11:30 的计划里就是周六那次，对应纽约周五收盘后；夏令时与冬令时都一样，唯一口径 `optimize.evidence.is_proposal_day`），其他运行为 skipped/not_proposal_day；获准手动演练可用 `python -m investment_assistant.tasks.daily run --manual --force-propose`（开关关着仍跳过）。不是另加一个定时任务。
- propose 位于 push 和 retro 之后，本次新提案进**下一次**周度复盘推送末尾：「本周提案 N 条，到 roadmap 页查看」。空证据为 success/no_evidence，不调用模型；开关关闭为 skipped/feature_disabled。
- 四开关默认全开，新运行冻结 features、续跑沿用、summary.features 留证。K2：关闭 portfolio_manager 是整节点 skipped/feature_disabled、不执行硬检查，fact「本版未检查」、validate_orders(None) 不作组合拦截，accounts 照常、Today not_computed；开启且硬结果已产出后 PM 模型失败才保留硬结果、Today degraded。
- agent+review 共用 1500 秒（研究请求和补查用剩余），PM 300、accounts 600、retro 1500、propose 600 秒，手动研究 900 秒；token 统一当日上限。研究请求遇 daily 正在跑仍 200 queued。模型未连接只跳过模型段，启用时硬检查与非模型记账照常。
- 新接口缺 031 为 503 migration_031_missing，daily 拒跑；综合核对入口为 `python -m investment_assistant.workflow check --session D`（D45，不另设 optimize check）。续跑仍看真实检查点 / resume.allowed；不把节点失败但已跑完的图写成可续跑。

## SEC 正文目录与日预算（v2.12，A2）

全部正文入口（sec_monitor、collect、手动族拉取、指定研究、补查）必须把**有效配置的 `filings.output_dir`** 投影为同一个 AdapterDeps.sec_text_dir，不能各建临时正文根。正文、索引与预算一同保留；不需要固定用户机器的私有绝对路径。

`<filings.output_dir>/.budgets/<ticker>-<session>.json` 是跨 scope、跨 Deps 实例共享的**日预算事实**：在文件锁内、下载前预留每 ticker/session 最多 3 份的名额，失败或进程中断也不返还；同 session 超时会阻断后续下载，下一 session 才再试。已索引或已存在正文不重复下载、不扣新下载名额，可回补索引和观察。

**不得在运行间清理 `.budgets`**，也不能通过换目录重置预算。维护 / 备份正文目录时连同预算保留；排查「为什么今天没下载」需一起查看预算与真实目标失败原因。首日可能 partial；成功发现文件不等于成功下载 / 存储。

数据采集统一由注册表按族锁调度，每族每 scope 记行；sec_monitor 正文记 `family=sec_text, detail.scope=sec_monitor`，collect 跳过本次已有正文。SEC/FRED/earnings 已有内部 fetch policy，不在外层再乘一次重试，attempts 记录真实主备总次数。正式 bars 无 Stooq 备源，财报日 Nasdaq 可用，见 [注册表](../features/data-registry.md)。获准后按 [验收清单](../roadmap/v2.12-v2.15-acceptance.md) 验证。

## 调度线程

调度线程随 API 进程启动（`api/app.py` 的 lifespan 拉起 `tasks/scheduler.py:run_forever`），`SCHEDULER_ENABLED=0` 或缺数据库时不启动。它每 `SCHEDULER_TICK_SECONDS`（默认 60 秒）醒一次：

- 取出 `enabled AND (next_run_at IS NULL OR next_run_at <= now)` 的任务（`FOR UPDATE SKIP LOCKED`）。
- 按 `REGISTRY` 派发；库里有、注册表里没有的任务记为 `unregistered job` 并跳过，不中断循环。
- 跑完按 `time_local` / `weekday_mask`（ISO 周几，如 `1-5`）/ `timezone` 算下一次运行时间，写回 `next_run_at`、`last_run_at`。
- 单个任务抛异常只记 `error`，循环继续。

### 任务注册表

v2.8（迁移 028）把原来的 `workbench`（06:30）、`workbench_review`（08:00）、`filings`（09:30）三个任务合成一个 `daily`，旧三行从 `scheduled_jobs` 删除；`job_reports.task` 的历史值 `workbench`、`workbench_review`、`filings` 保留只读。

| 任务 | 模块 | 调度 | 说明 |
| --- | --- | --- | --- |
| `daily` | `tasks/daily.py`（图在 `workflow/graph.py`） | 迁移 028 种子：北京 11:30，周二至周六（`weekday_mask = 2-6`，`Asia/Shanghai`），`enabled` 沿用原 `workbench` 行 | `daily-3` 的十三个节点，账户结算与模拟决策在 `review` 后、`push` 前，见[每日计划](../features/daily-plan.md#每日流程) |
| `sec_backfill` | `tasks/filings.py` 的 `backfill` | **不进 `scheduled_jobs`**，只手动 | 按日期回补 SEC 申报；调度器从不执行它，`GET /api/jobs/scheduled` 以 `manual_only: true` 的行列出它 |

北京周二至周六 11:30 = 纽约周一至周五 22:30（冬令时）/ 23:30（夏令时），都在当天收盘后、次日开盘前。LaunchAgent 不需要改：任务是 `scheduled_jobs` 里的行，由 API 进程内的调度线程执行。旧任务名（`workbench`、`workbench_review`、`filings`）调用 `POST /api/jobs/{name}/run` 返回 404。

每个任务页面上的 `?` 说明来自 `tasks/catalog.py` 的 `JOB_INFO`，随 `GET /api/jobs/scheduled` 的 `description` 字段下发；前端不再自带文案。改说明只改后端注册表。

迁移种子只是默认值。改时间或开关在「设置 → 定时任务」操作（`PATCH /api/jobs/scheduled/{name}`），下一个 tick 生效，不用重启。

### 手动运行

- 「工具 → 任务中心」的「立即运行」（`daily`）与 `sec_backfill` 的「按日期运行」（`{"date"}` 或 `{"from","to"}`，上限 92 天；未来日期、倒序、超限或缺日期返回 400）。
- API：`POST /api/jobs/{name}/run`，返回 `run_id`，用 `GET /api/runs/{run_id}` 轮询。
- 每日工作流手动运行**不推送**（`push` 记 `skipped: manual_run`，`retro` 的周 / 月复盘只生成不推送）；手动运行总是针对最近一个已收盘的交易日（纽约 17:00 后算当天收盘）。
- 手动运行（`POST /api/workbench/run` 或任务中心的「立即运行」）有两个 id：API 返回的 `run_id`（runner 的后台任务 id，页面轮询 `GET /api/runs/{run_id}`）与 `daily_run_id`（`daily-<UTC 时间戳>-<8 位 hex>`，路由先生成再传给 `tasks/daily.run`，它是检查点线程号、`job_reports.run_id`、`stage_runs.run_id` 和运行详情页的 id）。数据库记录一律用 `daily_run_id`。另一次执行正在进行（持有 `daily:run` 锁）时，手动运行（`daily` 与 `sec_backfill`，经 `services/jobs.trigger_job`）抛 `daily.DailyBusy`，接口返回 409 `daily_running`；探测只看锁（`pg_locks`）、不取锁，不会让 11:30 的定时执行撞上。`POST /api/jobs/daily/run` 同样返回 `daily_run_id`。
- 命令行：`python -m investment_assistant.tasks.daily run [--manual]`、`resume --run-id … --node …`、`show --run-id …`（节点表，不打印原始摘要）；`python -m investment_assistant.tasks.filings --date …` 或 `--from … --to …` 是 SEC 回补。

## 任务外壳与运行报告

`sec_backfill` 经 `tasks/_harness.py` 的 `run_task` 运行：生成 `run_id`，捕获异常为结构化 `status="error"`，写 `job_reports`（`task`、`run_id`、`status`、起止时间、`trigger_source`、`summary`），再交给通知层。`job_reports` 写入时剪掉 30 天前的行。

**`daily` 不经 `run_task`**（它的状态规则与 `summary.errors` 非空即 `error` 不同），由 `tasks/daily.py` 自己做外壳，仍用 `_harness._record` 写 `job_reports` 与运行日志：

- **每次执行一行**：首次运行一行（`trigger_source` 为 `scheduled` / `manual`），每次续跑再追加一行（`trigger_source = resume`），同一个 `run_id`；`target_start_date = target_end_date =` 该次运行的 session。
- **状态映射**：由节点的有效行推出（见下节）：失败 → `error`，降级 → `degraded`，否则 `success`；`summary` 增加 `graph_version`、`run_status`、`failed_node`、`resumed`、`stage_status`，续跑行另有 `resume_node`。
- **锁**：每次执行（定时、手动、续跑、`sec_backfill`）在专用连接上取会话级 advisory lock `hashtext('daily:run')`，`finally` 释放；拿不到时手动与续跑返回 `daily_running`，定时运行记一行 `error`、`summary.error = daily_running`（下一个 tick 不补跑）。`daily:run` 永远最先取（再取族锁与 SEC 监控文件锁），不死锁。
- **其它异常**：执行中出现节点之外的异常（检查点写入时数据库断开等）时，`daily.run` / `resume` 仍写一行 `job_reports`（`error`，`summary.error = aborted: <脱敏原因>`），尽量把本次执行没写行的节点补成 `failed`（第一个）与 `skipped / upstream_failed`，并发一次失败通知。
- **拒绝运行**：迁移 028、029、030 未执行，分别记一行 `error`、`summary.error = migration_028_missing` / `migration_029_missing` / `migration_030_missing`；检查点表缺失 → `checkpoint_unavailable`。这些前置失败都发一次失败通知，不让新工作流在旧表结构上运行。
- **失败通知**：运行状态为失败时经 `notifier.send_notifications('workbench', templates.daily_failed(…))` 发一条「⚠️ 每日工作流失败 · <session>」（失败节点、脱敏原因、未执行的节点数、续跑截止的北京时间，配置了 `workbench.public_url` 时附 `<public_url>/#tools/runs/<run_id>`），**每次执行至多一条**；降级不发。通知失败只记在 `summary.failure_notice`，不改变运行结果。推送类别键 `workbench`、`workbench_review`、`filings` 保留（D15）：`workbench` 是每日计划 + 失败通知，`workbench_review` 是周 / 月复盘，`filings` 是 SEC 披露。
- **连接失效通知**：本次执行的 `agent` 或 `review` 节点因 `agent_unavailable: not_connected` 整个跳过（或有单元中途遇到 `not_connected`），并且 `agent_connection.first_failure_since_ok(since=…)` 为真时，执行收尾（失败通知之后、写 `job_reports` 之前）经 `send_notifications('workbench', templates.agent_not_connected(…))` 发一条「⚠️ ChatGPT 连接已失效」（影响：今天的 Agent 建议与复查已跳过；处理：到「设置 → Agent 连接」重新连接）。判断只看**本次执行自己的行**（`attempt` 等于本次执行号），所以没有重新执行 `agent` / `review` 的续跑不会通知。**同一次失效只通知一次**：`since` 是最近一次**发出**这条通知的执行做检查的时刻（`summary.connection_notice.checked_at`，查不到则不限）——所以失效之后、命中之前夹着的休市日、在 `agent` 之前失败的运行、没有重新执行 `agent` 的续跑，都不会把起点推过一次还没通知的失效；失效发生在某次执行中途时，那次执行通知，下一次起点晚于失效、不再重复。通知发不出去（`sent: false`）时下一次执行会再试。结果记在 `summary.connection_notice = {sent, reason, checked_at}`（`reason` 为 `already_reported` 表示这次失效已通知过，`not_failed` 表示连接已经恢复；通知出错只记 `notice_failed: <异常类型>`，不改变运行结果，也不记异常文本）；规则计划、推送和复盘卡照常，所以这不是工作流失败。

「工具 → 运行记录」按任务筛选、每 15 秒刷新；`GET /api/jobs/reports?task=&limit=`（limit 1–200）。页面通过 `GET /api/events`（SSE）免刷新更新。

## 批处理采集的锁与拉取记录

每日工作流（定时与手动）的 `collect` 节点按固定顺序（`bars`、`news`、`sec_filings`、`fred`）持有会话级 Postgres advisory lock（键 `hashtext('data_family:<id>')`，专用连接、用完即放），避免与数据菜单的手动拉取同时写同一张数据表；`universe` 只读范围文件，不加锁。锁跨进程有效，命令行运行的批处理同样遵守。

`collect` 节点结束时，每个已记录的数据族向 `data_fetch_runs` 各追加一行（`job = daily`，`trigger` 为 `scheduled` / `manual_batch`，记录带该次运行的 `run_id`；显式 `tickers`/`pool` 的调用只写 `bars`、`news`、`sec_filings`、`fred` 四行，没有 `universe` 行）；纳指 100 范围成分的日线与财报日也在这个节点采集，计在 `bars` 族里。`screen` 节点为当天新入池、`collect` 没覆盖的成员补采资讯与 SEC 列表，在本节点取 `news`、`sec_filings` 两把族锁并追加两行（`detail.scope = screen_new_members`）。每个目标的结果（尝试次数、错误类别、HTTP 状态、行数、最新时点、耗时）记在 `detail.targets`，见[外部请求策略](../features/data-menu.md)。写入在独立事务里，失败（包括迁移 024 未执行）只记在运行报告的 `summary.data_status`，不影响运行结果。表结构与状态口径见[数据基础设计](../../../specs/2026-10-26-v2.3-data-foundation-design.md)。

`sec_monitor` 节点（下载器与监控两种模式）持 `sec_filings` 与 `sec_text` 两把族锁；扫描之后，`sec_text` 适配器（`scope = 'sec_monitor'`，D47）只对**本次扫描发现了新申报且扫描没失败**的标的下载并建索引（R27；扫描成功但没有新申报的标的按扫描记成功，来源 `sec`，其余标的由 `collect` 节点的 `sec_text` 行覆盖，并跳过本次已处理的）。结束后追加一行 `sec_text`（`detail.scope = 'sec_monitor'`，`job = daily`，`session` 取本次运行的 session，`trigger` 按本次运行取 `scheduled` / `manual_batch`），记录带该次运行的 `run_id`；`sec_backfill` 写的行是 `job = sec_backfill`、`trigger = manual_family`（族同为 `sec_text`）。目标按模式取实际采集清单：监控模式是监控观察名单文件（`sec_monitor.watchlist_path` 中「采集=是」的成员；成功时即 `summary.tickers`），下载器模式是 `config.watchlist`，与批处理的「当前池 + 持仓」不同，`detail.targets_source` 如实注明。整次采集失败记一条 `{"target": "*"}` 哨兵（不进 `target_count`）；Discord 投递这类非标的错误记在 `detail.non_target_errors`，不伪造成采集目标。写入失败只记在任务返回值的 `data_status`，不影响任务结果与运行报告；同时向 stderr 打一行 `[data_fetch_runs] write failed family=sec_text job=<job> trigger=<trigger> error=<脱敏原因>` 作为可检索落点。

## 阶段证据（十一个节点与 `attempt`）

每次 `daily` 运行（定时、手动、休市日）为每个节点向 `stage_runs` 写一行，`job = daily`，`stages_version = stages-5`，节点顺序固定为 `sec_monitor`、`collect`、`screen`、`regime`、`signals`、`plan`、`agent`、`review`、`accounts`、`push`、`retro`。**「十一行」读作十一条有效行**：`attempt` 默认 1，唯一键是 `(run_id, stage, attempt)`；一次续跑只给**重新执行的节点**（失败的节点，和它后面原来记了 `upstream_failed` 的节点）追加 `attempt = n+1` 的行，失败行与 `upstream_failed` 行是真实发生过的，不改写不删除；每个节点取最大 `attempt` 的行为有效行。已完成的节点只有 `attempt 1`，这就是它没有重跑的证明。`run_id` 与 `job_reports` 的 `run_id` 相同，`trigger` 为 `scheduled` / `manual_batch`（续跑沿用原运行的）。每行记状态（`success` / `partial` / `degraded` / `failed` / `skipped`）、输入与产出计数、按原因分组的跳过、逐目标失败、降级、产出引用（数据拉取行、申报、筛选、区制、候选、计划、Agent 运行、复查记录、账户权益与决策、复盘卡与新版计划的 id）与耗时；`duration_ms` 是节点真实起止之差（含重试等待与外部请求的全部等待），节点内部的子计时记在 `detail.timings.measured_ms`。表只追加，不在 `WORKBENCH_TABLES` 里。

- **节点级重试**：节点体抛出没人接住的异常时，等 60 秒（`NODE_RETRY_WAIT_S`）换新的累加器重试一次，第一次的错误记进该行的 `detail.retry = {first_error, waited_s}`；`service.Unavailable`（数据库不可达）不等、直接失败。第二次仍失败：写 `failed` 行（`reason = aborted: <脱敏异常>`），链停在这里；它后面还没写行的节点各写一行 `skipped`，原因 `upstream_failed`；运行状态为失败。阶段内部已经接住的失败（单个目标、单个 Agent 单元、整阶段的 `review` / `retro` 出错）仍按原来的方式记 `failed` / `partial`，不触发重试；唯一从"记失败继续"改成"停链"的是 `regime`（信号依赖当天区制）。
- **运行状态**：任一有效行 `failed` → 失败（`job_reports.status = error`）；否则任一 `partial` / `degraded` → 降级；否则成功（`skipped` 不影响）。
- **整节点跳过的原因**：`not_trading_day`（纽约休市，`push` 之前的九个节点）、`explicit_pool`（`screen`）、`manual_run`（`push`，手动运行不推送）、`already_pushed`、`task_disabled`、`not_due`（`retro`：不是交易日、周 / 月复盘也都没到期）、`upstream_failed`、`agent_unavailable: <原因>`（`agent`；`review` 在没有复查对象时同样如此，有对象时照常记账并标 `degraded`）、`migration_026_missing`（`review`）、`migration_027_missing`（`retro`）。
- **`retro`**：交易日先做复盘卡检测与起草（计数与 `detail` 见[每日计划 · 复盘卡检测](../features/daily-plan.md#复盘卡检测)，自己的 1500 秒窗口），再在任何日子判断周 / 月复盘（各自 `try`，一个出错不拦另一个；`detail.weekly` / `detail.monthly` 记 `action` 与推送结果；手动运行 `push = manual_run`）；休市日只做后一件；两件都没做才是 `not_due`，交易日不会记 `not_due`。出错只记该节点 `failed` 与 `errors` 一条 `retro: <原因>`，不影响已经发出的计划推送。
- 休市日的 `push` 发的是休市一行，记 `detail.closed = true`。
- 写入失败（含迁移 025 / 028 未执行）只记在运行报告 `summary.stage_status = {"written": n, "error": …}`，不进 `summary.errors`，不改变任务状态。
- **历史版本**：`stages-1`（7 行）、`stages-2`（8 行）、`stages-3`（9 行，`retro` 在 `push` 之前、没有 `sec_monitor`）、`stages-4`（10 行，没有 `accounts`）保留只读；`derivation stages` 与运行一览都按各自版本的节点集合核对。

### 账户结算与模拟决策（v2.11）

`accounts` 是原 `daily` 中的一个节点，没有另建定时任务。`workflow/nodes/accounts.py` 编排 `execution/paper/accounts.py` 的结算与 `analysis/agent/runner.py` 的模型用例，按以下顺序执行：

1. 所有模拟账户逐交易日结算此前挂单、止损、T1 和时间止损，写模拟成交及每日权益。
2. 写真实账户权益：补齐上次有行以来的交易日，并重算最近 5 个交易日；真实账户未开账时不写权益行。
3. 为模拟持仓写当日状态，使用买入时冻结的计划。
4. 先由照计划执行账户机械挂、换、撤单，再由 Agent 自管账户作本次交易日的组合决策，订单最早下一交易日成交。

- **预算**：`ACCOUNTS_AGENT_DEADLINE_S = 600`，从账户节点开始起算，所有账户的组合决策共用；达到窗口后不再启动新的账户模型调用。它独立于 `agent` / `review` 的 1500 秒窗口，也独立于 `retro` 的 1500 秒起草窗口；token 仍由 `agent_runs` 汇总并计入同一个每日上限，前面节点已用的 token 会减少剩余额度。
- **补结算**：模拟账户从 `settled_through` 的下一交易日起逐日结算到本次 session，补权益和成交；缺跑的中间日期不补造决策。`collect` 覆盖模拟持仓和在挂买单标的；缺当日日线时该标的当天不成交，记录 `bars_missing`，估值沿用旧收盘时另标 `stale`。
- **幂等与隔离**：账户锁保护读取、检查与写入；`paper_decisions` 的 `(account_id, session)` 唯一，同一天已有决策（包括 `skipped`）就沿用，不重复调用模型或下单。结算水位、订单状态和链消耗记录防止重复成交；账户间的成交、现金与持仓分别读取，不改全局 Agent 链状态。
- **模型不可用**：断连、额度、模型错误、每日 token 上限或阶段窗口耗尽时，记 `skipped` 与安全原因；此前的订单结算和自动退出已执行，照计划执行账户照常运行。单个账户出错记逐账户失败，其余账户继续。
- **运行详情与核对**：`accounts` 的结果列出账户、结算数量、权益、持仓与决策状态；账户页展开订单和决策证据，见[账户与模拟交易](../features/accounts.md)。只读命令行为 `python -m investment_assistant.views.account show [--account UUID或名称] [--session YYYY-MM-DD]`。

真实库备份、迁移 030 的单事务执行与服务重启须在单独授权后进行；本地代码与文档准备不代表这些步骤已执行。迁移须先于运行新代码，缺 030 时账户接口返回 503，`daily` 与续跑前检拒绝运行。真实开账和模拟账户首次运行的结果仍需真实数据验收。

### 检查点、续跑与重跑 Agent

- **检查点**：`PostgresSaver`（与 Agent 子图共用 `checkpoints` / `checkpoint_writes` / `checkpoint_blobs` 三张表），`thread_id = run_id`，`graph.invoke(…, durability='sync')`，每个节点结束同步落检查点；不清理。表缺失时 `daily` 拒绝运行（`checkpoint_unavailable`）。
- **续跑**：`POST /api/jobs/daily/resume`（body `{run_id, node}`，`services/jobs.resume_daily`）同步做前置检查——`workflow.resume_check`（不写任何行）通过、`daily:run` 锁没被占——然后后台 `tasks.daily.resume` 取锁、从检查点接着跑，立即返回 202 `{run_id, node, status: 'pending', attempt, runner_id}`（`runner_id` 是后台任务 id，`daily_resume-…`）；续跑的 `attempt = 该运行已有的最大 attempt + 1`。拒绝条件（错误体 `{"error": "<代码>"}`；缺 `run_id` / `node`、`run_id` 格式不对或 `node` 不在 `stages-5` → 400）：没有这次运行（404）、运行没有失败的节点（409 `not_failed`；检查点停在一个**没有任何行**的节点——进程在节点中途被杀——也按此拒绝，这样的运行只能整次重新运行，已知限制）、节点不是失败的那个（409 `node_mismatch`）、现在已过下一个交易日的纽约 17:00（409 `resume_window_closed`）、检查点图版本与当前 `DAILY_GRAPH_VERSION` 不一致（409 `graph_version_changed`）、另一次执行正在进行（409 `daily_running`，最后检查），迁移 028 / 029 / 030 未执行或数据库不可达 → 503。续跑沿用原运行的 `session` 与 `trigger`，普通节点仍用原运行的 `cutoff_at`：采集类节点丢掉来源时间晚于截止时间的资讯和申报（数量记 `detail.after_cutoff_dropped`），日线只写到 session，财报日取当时的值（已知限制）。
- **`accounts` 的独立截止时间**：`service.stage_accounts` 在每次节点尝试开始时只读一次当前墙钟，作为该次账户截止时间，session 不变；本轮上游在节点开始前已提交的计划可用，此后提交的版本仍被排除。先查询并复用同账户同 session 的已有决策，保留原 `detail.cutoff_at`、证据与订单；尚未决策的账户才使用新尝试的截止时间。新时点写入阶段与决策的 `detail.cutoff_at`，有证据时同时写入 `account.cutoff_at`，决策 API 回显该决策所用时点；各数据源仍按 session 与对应截止时间过滤。
- **Agent 预算窗口**：`agent` 与 `review` 共用一个窗口，从 `agent` 节点开始起 1500 秒（状态里存墙钟 `agent_started_at`，节点内换算成单调时钟）；`accounts` 从账户节点开始另有 600 秒组合决策窗口，`retro` 的复盘卡起草有自己的 1500 秒；token 仍共享每日上限，续跑时从续跑的那个节点重新起算。`BATCH_BUDGET_S` 与 `batch_over_budget` 已删除。
- **重跑 Agent（`max_attempts`）**：单元的 Agent 尝试闸只数最近一次**重置**之后的运行；重置是追加一行 `agent_attempt_resets`（`request_id` 幂等），不删旧运行，`attempt` 字段仍按全部历史运行数递增。入口是运行详情页与计划页（`POST /api/workbench/agent-runs/rerun` → `service.rerun_agent`；同步执行，条件与错误码见[Agent 轨道](../features/agent-track.md#预算与重新运行v28)）。
- **一次性故障注入（只用于 Task 7 的续跑演练）**：环境变量 `INVESTMENT_ASSISTANT_FAULT_ONCE=<节点 id>` 存在、且本 `run_id` 的该节点还没有任何行时，`workflow.node` 对该节点的两次尝试都抛 `RuntimeError('fault injection')`（续跑时不再注入）。演练用完立即从 `.env` 删掉这个变量并重启，不要让它留在服务环境里。

### 运行一览（v2.6）

阶段证据在「今日」顶部以「运行一览」展示，数据来自只读接口 `GET /api/workbench/run-overview?session=YYYY-MM-DD[&run_id=…]`（`session` 缺省为最近已收盘交易日），实现是 `views/runs/overview.py`。它**只读 `stage_runs`**（一个只读事务，不读别的表、不调用外部数据源），每个阶段返回与行逐字段相同的数字（`input_count`、`output_count`、`skips`、`failures`、`degradations`、`reason`、`duration_ms`），另加后端按模板拼好的一句话 `sentence`、原因文案（`reason_text`、`skips[].text`、`degradations[].text`）、状态文案 `status_text` 和该阶段的概念卡 `concepts`；整次运行另有 `headline`（如「定时运行 · 11:30 开始 · 用时 3 分 12 秒 · 11 个阶段：成功 9 · 降级 1 · 跳过 1」，时间为北京时间；运行失败时改为「10 月 29 日的运行在「数据采集」失败（已自动重试 1 次）：<原因>；其后 9 个节点未执行」，续跑后另加「（第 2 次执行，从「数据采集」续跑）」）。`sec_monitor` 的句子是「扫描 n 只，新归档 k 份申报，通知 m 条」（下载器模式「下载器模式：新下载 k 份申报」，目标失败时后缀「；f 只失败」）。`retro` 的句子在下面两种之后加「；周度复盘：已推送 / 已生成未推送 / 未到期；月度：…」（休市日没有复盘卡检测，只有这半句），`not_due` 整句「今天没有到期的复盘」；原来的两种：有事件或起草时「新结束 {events} 笔（平仓 a · 止损 b · 到 T1 c · 过期 d · 作废 e，只列非零项），新建复盘卡 {new_cards} 张；起草 {ok}/{attempted} 张；待审定 {to_review} 张」（`attempted` = 本次真正起草的张数，即成功 + 作废 + 出错，被跳过的不算；旧行没有 `detail.drafts` 时退回输入数），都为 0 时「没有新结束的计划或平仓；待审定 {to_review} 张」，其概念卡是 `review.retro_card`、`review.retro_end`。

- **同一天多次运行**：默认显示该交易日最后开始的、**不是休市空跑**的那次（休市空跑 = `push` 之前的阶段全部 `skipped` 且原因为 `not_trading_day`）；只有休市空跑时才显示它，`headline` 写「纽约休市，未运行（只推送一行休市说明）」。`runs` 列出同日全部运行（`run_id`、`trigger`、`trigger_text`：定时 / 手动 / 休市、`stages_version`、开始与结束时间、行数、`closed`），`run_id` 指定其中一次；不属于该日 → 404，日期非法 → 400，数据库不可用 → 503。
- **边界**：`stage_runs` 表不存在 → `ready: false`、`note`「迁移 025 未执行，暂无阶段证据」；这一天没有任何行 → `note`「这一天没有阶段证据：当天没有运行，或运行早于 v2.4（2026-10-27 起才记录）」；`stages-1` 运行在 `review` 与 `retro` 处、`stages-2` 运行在 `retro` 处补一行 `not_recorded` 占位（计数为 `null`；旧运行按 v2.7 的九个阶段画，`retro` 仍叫「复盘卡」）；同一节点有几行（续跑）时取最大 `attempt` 的那行；缺某个阶段行 → `missing`「这一阶段没有证据行（记账失败，见运行报告）」；但所选运行没有它那个版本最后一个阶段（`stages-4` / `stages-5` 是 `retro`，更早的版本是 `push`）的行、且缺的阶段都排在最后一个已记录阶段之后时（批处理还在跑，或中途被终止），缺的阶段改写「还没有这一阶段的记录：运行可能仍在进行，或中途被终止」，摘要末尾加「（可能仍在运行）」，状态仍是 `missing`；注册表之外的阶段排在最后，句子「未知阶段」。
- **原因文案**：先查 `run_overview.REASON_TEXT`（只放 `agent.REASON_TEXT` 没有的代码，含降级 kind 与规则跳过原因），再查 `agent.REASON_TEXT`，不认识的代码原样显示；带参数的 `agent_unavailable: <x>`、`aborted: <x>`、`<规则 id>:<原因>` 按规则拼，其中的参数 `<x>` / `<原因>` 是已知代码时也翻译（如 `agent_unavailable: not_configured` →「Agent 不可用：未配置」，`trend_reclaim:insufficient_history` →「趋势回踩修复：日线不足 200 根」），`aborted:` 后的异常摘要原样显示。
- **命令行（只读）**：`python -m investment_assistant.views.runs.overview --session YYYY-MM-DD [--run-id ID]`，先打印 `headline`，再逐行打印 `阶段 · 状态 · 句子 · 输入/产出`；没有阶段证据（打印说明）退出码 1，参数错误、`--run-id` 不属于该日或数据库不可用退出码 2。

## 通知

配置是 `config.py` 的 `NotifyConfig`，「设置 → 推送配置」写入 `notify_settings` 后覆盖它：

| 字段 | 说明 |
| --- | --- |
| `channel` | 全局单选：`discord` 或 `email` |
| `discord_enabled` | Discord 总开关 |
| `webhooks` | `{频道: url}`；未配置时退回环境变量 `DISCORD_WEBHOOK_SIGNALS` / `_DAILY` / `_EARNINGS` |
| `task_channels` | 推送类别 → 频道；默认 `workbench`（每日计划与工作流失败通知）、`workbench_review`（周 / 月复盘）→ `signals`，`filings`（SEC 披露）→ `earnings`；三个键保留为推送类别，任务合并不改名（D15） |
| `task_enabled` | 每个推送类别的开关 |
| `email` | 发件人、收件人、SMTP 主机与端口；密码只取环境变量 `SMTP_PASSWORD` |

派发顺序：总开关关 → 不发；该任务开关关 → `task_disabled`；没有频道 → `no_channel`；否则发送，失败返回 `send_failed: …`，不让任务失败。每日推送由 `push` 节点发、周 / 月复盘推送由 `retro` 节点发，结果写在运行报告的 `summary.push` 与 `retro` 行的 `detail` 里；`daily` 在运行失败时另发一条「每日工作流失败」（见上）。

每日推送在「🆕 新计划」之后多一条「🧩 组合检查」（v2.14）：可一起做几条、被拦几条与原因、暂缓几条（现金不足）、宿主先后和组合经理的一句理由（Agent 不可用时写「只有硬结果」）。只有真实账户当日有硬结果才发；`portfolio_manager` 开关（取本次运行冻结的值）关着、节点没跑或没有迁移 031 时不发这一条，不用旧运行留下的同日检查顶替。周度复盘推送（`retro` 节点）末尾加一行「本周提案 N 条，到 roadmap 页查看」：N 是上一次成功的周度复盘推送之后 `propose` 节点写下的提案数（周六的复盘推送在 `propose` 之前，所以提案进下一次推送）；没有迁移 031 时不加这一行。

验证：「设置 → 推送配置」点「验证」发测试消息（`POST /api/settings/notify/test`，body `{"channel": "signals"}`）。webhook 只返回「是否已配置」，不回显明文。
