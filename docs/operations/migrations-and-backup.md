# 迁移与备份

## 迁移清单

迁移文件只增不改：已执行的文件不再编辑，改 schema 一律新增编号。业务表只追加，迁移不回填、不改写旧行（`021` 删除 A 股数据是唯一例外；`033` 整体删除 v2 的业务表，见下文）。

| 编号 | 内容 | 组 |
| --- | --- | --- |
| 001 | `market_signals`：SPY/VIX 市场信号 | v1 遗留（022 已删） |
| 002 | `watchlist_items`：观察名单 | 共享 |
| 003 | `ticker_signal_snapshots`：个股趋势快照 | v1 遗留（022 已删） |
| 004–005 | `strategy_scores` 及指向快照的外键 | v1 遗留（022 已删） |
| 006 | `job_reports`：运行报告（写入即剪枝，保留 30 天） | 共享 |
| 007 | `scheduled_jobs`：调度表，种子 `metrics`/`filings`/`scores` | 共享 |
| 008 | `notify_settings`：通知设置 | 共享 |
| 009 | `event_studies`：事件研究 | v1 遗留（022 已删） |
| 010 | `job_reports` 加指定日期与触发来源 | 共享 |
| 011 | `filings`：SEC 文件索引 | 共享 |
| 012 | 通知渠道单选（Discord / 邮件）与邮件配置 | 共享 |
| 013 | 研究观察卡表（及已删除的 A 股表，见 021） | v1 遗留（022 已删） |
| 014 | `us_research_*`：美股财季数据与指引 | 主线 · 基本面 |
| 015 | `ticker_signal_snapshots`、`strategy_scores` 加来源元数据（可空） | v1 遗留（022 已删） |
| 016 | 工作台 P0：日线、资讯、候选、解读、决定、成交、周度复盘；`workbench` 任务（北京 06:30，周二至周六） | 主线 |
| 017 | P1：交易计划、财报日、资金快照、出入金、月度复盘；`signal_decisions.plan_id` | 主线 |
| 018 | P2：`pool_tiers`、`macro_observations`、`market_regimes`、`rule_backchecks` | 主线 |
| 019 | P3：`agent_runs`、`agent_tool_calls`；`trade_plans.kind` 放宽为含 `agent` | 主线 |
| 020 | P4：`agent_runs.mode`、`signal_briefs.agent_run_id`、`review_pushes`；`workbench_review` 任务（北京 08:00，插入为关闭） | 主线 |
| 021 | 删除 A 股：`cn_*` 两张表、CN 观察名单行、`CN:` 研究卡、`ashares` 任务行；`job_reports` 历史保留 | 共享 |
| 022 | 删除 v1 遗留：`strategy_scores`、`ticker_signal_snapshots`、`market_signals`、`event_studies`、`research_observation_card_versions`、`research_observation_cards` 六张表与 `metrics`、`scores`、`event_study` 调度行；`job_reports` 历史与已冻结的 `backcheck-1` 记录保留 | 共享 |
| 023 | v2.2：`screen_runs`、`screen_results`、`pool_tiers.source`（默认 `watchlist`）；`filings` 任务改为北京 09:30（只改仍是纽约时区的行） | 主线 + 共享 |
| 024 | v2.3：`data_fetch_runs`：每次某族被拉取一次追加一行记录成败与原因；只建表与索引，不动旧表（2026-10-27 已在真实库执行，见下文） | 主线 |
| 025 | v2.4：`stage_runs`：每次 `workbench` 任务、每个阶段追加一行证据（计数、原因、产出引用、耗时）；只建表与索引，不动旧表（2026-10-27 已在真实库手动执行，见下文） | 主线 |
| 026 | v2.5：计划时间线。新建只追加的 `agent_plan_versions`（版本链）、`plan_reviews`（每日复查记录，每条链每个交易日至多一条有效批处理复查）、`user_inputs`（用户输入）；`signal_decisions` 加可空列 `agent_plan_id`、`review_view`；放宽 `agent_runs.mode`（加 `review`、`revise`）及两条相关 CHECK、`agent_tool_calls.phase`（加 `review`）、`stage_runs.stage`（加 `review`）；`idx_trade_plans_agent_unique` 只管 `agent-1`。不改旧行、可重跑；三张新表不进 `WORKBENCH_TABLES`，就绪与否由 `repository.timeline_ready` 判定。对旧代码无害（只新增表 / 列、放宽约束）。必须先于重启执行（spec D19）；2026-10-28 已在真实库手动执行，见下文 | 主线 |
| 027 | v2.7：我的成长闭环。新建十一张只追加的表：`review_cards`、`review_card_drafts`、`review_card_verdicts`（复盘卡及其起草与审定）、`discussions`、`discussion_turns`（就此计划讨论）、`knowledge_items`、`knowledge_versions`、`knowledge_status`（我的知识）、`knowledge_drafts`、`knowledge_draft_verdicts`（教训草稿及其决定）、`knowledge_citations`（计划与复查引用了哪一版知识）；放宽 `agent_runs.mode`（加 `retro`、`discuss`）、`agent_tool_calls.phase`（加 `retro`、`discuss`）、`stage_runs.stage`（加 `retro`）。约束名查不到时按定义找出后删除（同 026 的 `DO` 块），不改旧行、不加种子行、可重跑；十一张新表不进 `WORKBENCH_TABLES`，就绪与否由 `repository.growth_ready` 判定。对 v2.6 代码无害（只新增表、放宽约束）。必须先于重启执行（spec D29）；真实库执行需单独授权，见下文 | 主线 |
| 028 | v2.8：每日工作流。`stage_runs` 增加 `attempt`（默认 1）与 `job` 取值 `daily`、`stage` 取值 `sec_monitor`，唯一键改为 `(run_id, stage, attempt)`（约束名查不到时按定义找出后删除，同 026 / 027 的 `DO` 块）；新建 `agent_connections`（单行，**含令牌**）、`agent_connection_events`、`agent_attempt_resets`；`scheduled_jobs` 删 `workbench`、`workbench_review`、`filings` 三行并插入 `daily`（北京 11:30、周二至周六，`enabled` 沿用原 `workbench` 行，`next_run_at` 直接写下一个 11:30）；加索引 `idx_job_reports_run_id`。不改任何证据行、可重跑；新表不进 `WORKBENCH_TABLES`，就绪与否由 `repository.daily_ready` 判定（`attempt` 列、重置记录表、`daily` 调度行都在）。**必须先于重启执行**：028 未执行时 v2.8 代码拒绝运行 `daily`（`job_reports` 记一行 `error / migration_028_missing`），不做半新半旧的降级；028 执行后、重启前旧代码的调度器只会对 `daily` 记一条「未注册」警告。真实库执行需单独授权，见下文 | 主线 |
| 029 | v2.9：可执行的计划。`trades` 加可空 `plan_id`（指向 `trade_plans`）与 `trades_link_one_check`（`plan_id` 与 `decision_id` 不并存），建部分索引 `idx_trades_plan`；`trade_plans` 加 `entry_type`（`zone_limit` / `breakout_stop` / `next_open`）、`max_hold_sessions`（3–30）两列，两列同有同无，并把 017 内联的价位 CHECK 换成具名的 `trade_plans_levels_check`（持仓期 Agent 计划可以止损高于入场价，R3）；新建三张只追加的表：`chain_retriggers`（再次触发并入哪条链）、`holding_status`（每个持仓标的每个复查交易日一行，`stop_mode` 取 `initial` / `trailing`）、`plan_notes`（给自己看的备注，接口支持更正 / 撤回，页面暂未提供）；放宽 `plan_reviews` 的 `decided_by` CHECK（再次触发的直接修订可以只带修订运行）。约束名查不到时按定义找出后删除（同 026 – 028 的 `DO` 块），不改旧行、不加种子行、可重跑；三张新表不进 `WORKBENCH_TABLES`，就绪与否由 `repository.executable_ready` 判定（三张表、`trades.plan_id`、两个执行字段都在）。对 v2.8 代码无害（只新增列 / 表、放宽约束）。**必须先于重启执行**：029 未执行时 v2.9 代码拒绝运行 `daily`（`job_reports` 记一行 `error / migration_029_missing`），`plan-table`、`plan-history`、`holding-status`、`plan-notes`、`linkable-plans` 与带 `plan_id` 的成交返回 503；除这些 503 外，其余读写成交 / 计划的入口（例如 `/today`、不带 `plan_id` 的 `POST /trades`、周报生成）因为查询里含 029 的列，直接报错（500 / 任务失败）。不做半新半旧的降级。价位约束按 spec §21.1 R3 带 `kind = 'agent'` 与 `inputs->>'chain_mode' = 'holding'` 两个条件，并用 `COALESCE(inputs->>'chain_mode', '')` 兜底：`inputs` 没有 `chain_mode` 键时 `->>` 得 NULL、CHECK 会把 NULL 当作通过，比 spec 原文更严（实现偏差）。真实库执行需单独授权，见下文 | 主线 |
| 030 | v2.11：账户与模拟交易（spec §7）。新建六张表：`accounts`（真实 / 模拟、策略、初始现金与开户日、参数、关闭，部分唯一索引保证全库恰一个真实账户，另有只前进的 `settled_through` 水位列）、`account_param_changes`（参数与开账变更，下一交易日生效）、`account_equity_days`（每日权益，只追加，读取每 `(account_id, session)` 取 `computed_at` 最新）、`paper_orders`（订单全生命周期，状态列可更新，部分唯一索引「同账户同链至多一个在挂买单」）、`paper_decisions`（每账户每交易日一行，`UNIQUE (account_id, session)`）、`paper_chain_done`（每账户每链的消耗记录）；`trades`、`account_snapshots`、`cash_flows`、`holding_status` 各加 `account_id UUID NOT NULL DEFAULT <真实账户 id>` 与三个索引，`trades` 另加 `recorded_by`（`user` / `host`）与 `detail JSONB`；`holding_status` 的唯一约束从 `(ticker, session)` 换成 `(account_id, ticker, session)`（旧约束按 `pg_constraint` 定义找出后删除，同 029 的做法）。三处放宽：`stage_runs.stage` 加 `accounts`（`stages-5` 的 11 个阶段）、`agent_runs.mode` 加 `portfolio` 且 `ticker` / `signal_date` / `pool` 转可空（新约束 `agent_runs_portfolio_ticker_check`：`ticker` 为空当且仅当 `mode = 'portfolio'`，D27）、`agent_tool_calls.phase` 加 `portfolio`。**本项目第一次写种子行**：只插默认真实账户一行（固定 id `00000000-0000-0000-0000-000000000001`，初始现金与开户日为空），`ON CONFLICT (id) DO NOTHING` 可重跑；旧行由四列默认值归入真实账户，不改写任何旧行。六张新表不进 `WORKBENCH_TABLES`，就绪与否由 `repository.accounts_ready` 判定（六表与 `trades.account_id` 都在）。对 v2.10 代码无害（只新增表 / 列、放宽约束、换唯一键——旧行都满足新键）。**必须先于重启执行**（spec D22）：030 未执行时 v2.11 代码拒绝运行 `daily`（`job_reports` 记 `error / migration_030_missing`），`/api/accounts/*` 返回 503。真实库执行需单独授权，见下文 | 主线 |
| 031 | v2.12–v2.15 五层与 Agent 角色：新建十二张表 `feature_flag_changes`、`data_requests`、`sector_classifications`、`research_requests`、`research_versions`、`portfolio_checks`、`roadmap_goals`、`roadmap_versions`、`roadmap_decisions`、`roadmap_evidence`、`roadmap_proposals`、`roadmap_briefs`。放宽 `stage_runs.stage`（13 阶段）、`data_fetch_runs.family`（9 族）/ `trigger`（5 来源）、`agent_runs.mode` / 研究 pass / 空 ticker 与 `agent_tool_calls.phase` CHECK；增加 `price_bars.source`（yfinance/stooq）、`earnings_dates.source`（yfinance/nasdaq）CHECK，`news_items_source_check` 不变。不改旧行、不加种子、可重跑。`core.schema.five_layers_ready` 检查十二表与允许 portfolio 的具名阶段 CHECK；未就绪时 daily 的 run / resume / ensure_ready 拒绝并记 `job_reports: error / migration_031_missing`。执行顺序：**备份 → 031 → roadmap import → 重启**；真实库各步需单独授权，导入须等整版代码就绪，见下文 | 主线 |
| 032 | v3：七张 `v3_` 表（`v3_runs`、`v3_candidates`、`v3_verdicts`、`v3_plans`、`v3_trades`、`v3_equity`、`v3_tool_calls`），只建表，不动任何旧表；2026-11-06 已在真实库执行，记录见 [v3-facts.md](v3-facts.md#迁移-032-的执行记录2026-11-06真实库) | v3 |
| 033 | 删除 v2 的业务表：57 张表（账户、成交、计划、Agent 运行与连接、复盘、成长闭环、五层、调度与运行报告、通知设置等）加 LangGraph 的四张检查点表（有就删）；保留 11 张事实表、`feature_flag_changes` 与七张 `v3_` 表。一个事务、全部 `IF EXISTS`、不用 `CASCADE`、可重跑。**已在一次性库验证，真实库尚未执行，待用户授权**，步骤见下文 | 删 v2 |

016–031 建出的业务表，除 033 的保留清单外都由 033 删除；在 033 于真实库执行之前，它们仍在库里，保留的代码不读不写它们。

LangGraph 的检查点表不是迁移建的（v2 时由 `analysis.agent setup` 命令建，该命令随旧系统移除），033 用 `DROP TABLE IF EXISTS` 一并删除。

## 新库

```bash
INVESTMENT_ASSISTANT_DATABASE_URL=... .venv/bin/python -m investment_assistant.migrate
```

`migrate` 入口按文件名顺序执行未执行过的迁移，并记入 `schema_migrations`，重复运行不做任何事。新库从 001 一路执行到 033，最终只剩保留的表。`migrate` 不读 `.env`，连接串要在进程环境里给。

## 真实库：备份后单事务执行

真实库的迁移曾逐个手动执行（下面是那套做法，也是 033 的备份写法）：

```bash
pg_dump -Fc "$INVESTMENT_ASSISTANT_DATABASE_URL" -f .local/us-workbench/backups/investment_assistant-before-<编号>-<时间戳>.dump
shasum -a 256 .local/us-workbench/backups/investment_assistant-before-<编号>-<时间戳>.dump
psql "$INVESTMENT_ASSISTANT_DATABASE_URL" --single-transaction -v ON_ERROR_STOP=1 -f migrations/<编号>_<名称>.sql
```

执行后统计受影响表的行数并记录下来。

> 历史注意：2026-11-06 之前真实库的 `schema_migrations` 只记录到 `017`，`018`–`031` 都是用 `psql` 手动执行的，当时不能对真实库运行 `migrate` 入口。2026-11-06 执行 032 时已核对物理 schema 并补记追踪行，此后追踪表与物理 schema 一致，`migrate` 入口可以使用，经过见 [v3-facts.md](v3-facts.md#迁移-032-的执行记录2026-11-06真实库)。

恢复备份：`pg_restore --clean --if-exists -d "$INVESTMENT_ASSISTANT_DATABASE_URL" <备份文件>`，执行前先停服务。

以下 022–031 各节是 v2 时期在真实库执行迁移的步骤与记录，按当时的代码与服务写成，保留作历史；其中涉及的表由 033 删除。

### 022 的真实库执行（逐步手动，每步先征得用户同意）

代码先上（提交合入并重启 LaunchAgent）之后才执行 022；期间 `scheduled_jobs` 里的 `metrics`、`scores` 行只会被记为 `unregistered job` 并照常重排。

1. 只读预检：确认 `schema_migrations` 最大编号为 `017`；统计 6 张表行数；查 `pg_constraint`、`pg_depend` 确认 6 张表之外没有对象依赖它们。
2. 全库备份：

   ```bash
   pg_dump -Fc "$INVESTMENT_ASSISTANT_DATABASE_URL" -f .local/us-workbench/backups/investment_assistant-before-022-<时间戳>.dump
   ```

3. 6 张表单独导出（便于单表恢复）：

   ```bash
   pg_dump -Fc "$INVESTMENT_ASSISTANT_DATABASE_URL" \
     -t market_signals -t ticker_signal_snapshots -t strategy_scores -t event_studies \
     -t research_observation_cards -t research_observation_card_versions \
     -f .local/us-workbench/backups/investment_assistant-v1-legacy-tables-<时间戳>.dump
   ```

4. 执行（手动 psql，不走 `migrate` 入口）：

   ```bash
   psql "$INVESTMENT_ASSISTANT_DATABASE_URL" --single-transaction -v ON_ERROR_STOP=1 -f migrations/022_drop_v1_legacy.sql
   ```

5. 验证：6 张表的 `to_regclass` 都为 null；`scheduled_jobs` 无 `metrics`、`scores`、`event_study`，`workbench`、`workbench_review`、`filings` 行还在；手动跑一次工作台批处理成功。

#### 执行记录（2026-10-25，手动 psql 执行）

| 步骤 | 结果 |
| --- | --- |
| 上线前置 | `notify_settings` 只读查询：`filings` → `earnings`、`workbench` → `signals`、`workbench_review` 未存（默认 `signals`），无主线任务路由到 `daily`；代码 `691641c` 已上线、LaunchAgent 已重启 |
| 1. 只读预检 | `schema_migrations` 17 条、最新 `017_trade_plans.sql`；行数 `strategy_scores` 568、`ticker_signal_snapshots` 568、`market_signals` 71，`event_studies`、`research_observation_cards`、`research_observation_card_versions` 为 0（共 792 kB）；唯一外键 `fk_strategy_scores_snapshot` 两端都在 6 张表内；`pg_depend` 中 6 张表之外无依赖对象 |
| 2. 全库备份 | `.local/us-workbench/backups/investment_assistant-before-022-20261025-204403.dump`（4,268,871 字节，sha256 `e77c76ad0db7fd68022d017cb08b5bb18e693bea51b9a7e19d2212fddd480e2b`）；`pg_restore --list` 可读，38 张表与线上一致 |
| 3. 遗留表导出 | `.local/us-workbench/backups/investment_assistant-v1-legacy-tables-20261025-204504.dump`（38,244 字节，sha256 `0bffe4d87b28a10dd361832cc31b65f60ed5bafda0d980ff50bdaa4e61ea8e81`）；6 张表、4 个序列、3 个索引、外键都在，行数与预检一致 |
| 4. 执行 | 20:48:30 +0800，`psql --single-transaction -v ON_ERROR_STOP=1`：`DELETE 2`（`metrics`、`scores`；库里本无 `event_study` 行）与 6 次 `DROP TABLE`，退出码 0；`schema_migrations` 仍只到 `017` |
| 5. 验证 | 6 张表 `to_regclass` 均为 null，`public` 表数 38 → 32；`scheduled_jobs` 只剩 `filings`、`workbench`、`workbench_review`；`job_reports` 的 `metrics`、`scores` 历史各 9 行保留；`us_research_observations`（29,648 行）、`us_research_collection_state`、`price_bars`（3,802 行）未动；服务接口全部 200，健康检查数据库与调度线程 up；手动批处理 `workbench-20261025125234-aff00057` `success`（交易日 2026-10-23，`errors` 为空，手动运行不推送） |

单表恢复：`pg_restore -d "$INVESTMENT_ASSISTANT_DATABASE_URL" .local/us-workbench/backups/investment_assistant-v1-legacy-tables-20261025-204504.dump`，再按需插回两行调度。

### 023 的真实库执行（逐步手动，每步先征得用户同意）

023 只新增表和带默认值的列，旧代码照常运行，所以先迁移、后重启（反过来新代码会读不存在的列）。

#### 执行记录（2026-10-26，手动 psql 执行）

| 步骤 | 结果 |
| --- | --- |
| 1. 只读预检 | `schema_migrations` 最新仍为 `017_trade_plans.sql`；`screen_runs`、`screen_results` 不存在，`pool_tiers` 无 `source` 列、共 8 行；`public` 表数 32；`filings` 为 `America/New_York 20:30`（周一至周五，下次 2026-10-27 08:30 +0800） |
| 2. 全库备份 | `.local/us-workbench/backups/investment_assistant-before-023-20261026-101340.dump`（4,235,781 字节，sha256 `dae881b7e2e0e263363a0934985cb079347e602012e73d4f2f6a2197d5515480`）；`pg_restore --list` 可读，32 张表与线上一致 |
| 3. 执行 | 用户在终端手动执行（10:13 备份之后、10:25 核对之前），`psql --single-transaction -v ON_ERROR_STOP=1 -f migrations/023_screen_and_beijing_jobs.sql`；`schema_migrations` 仍只到 `017` |
| 4. 验证 | `screen_runs`、`screen_results` 已建（各 0 行），索引 `idx_screen_runs_session`、`idx_screen_results_ticker` 在；`pool_tiers.source` 为 `text NOT NULL DEFAULT 'watchlist'`，CHECK `pool_tiers_source_check` 在，8 行旧数据全部为 `watchlist`；`filings` 为 `Asia/Shanghai 09:30`（周一至周五），`next_run_at` = 2026-10-27 09:30 +0800（周二，当天 09:30 已过）；`workbench`（06:30 周二至周六）与 `workbench_review`（08:00 每天）未变；`public` 表数 32 → 34 |
| 5. 重启服务 | 重启前进程 pid 1196 启动于 2026-10-25 18:04（旧代码，`GET /api/workbench/screen` 为 404），无运行中任务；10:28:30 +0800 `launchctl kickstart -k`，新进程 pid 42156 启动于 10:29:00；`/api/health`：数据库、调度线程（轮询运行中）、SEC、Yahoo、Discord、FRED 均 up，3 项 `systemctl` 检查在 macOS 上为 down（重启前即如此，与本次无关）；`/api/workbench/screen`（`report: null`，尚无筛选记录）、`/api/workbench/today`、`/api/jobs/scheduled`、`/api/status` 均 200；`/api/jobs/scheduled` 三个任务都是 `Asia/Shanghai` 并带 `description` |

服务重启与手动验收（spec §11.2）见 [v2.2 验收记录](../archive/v2/roadmap/v2.2-acceptance.md)。

### 024 的真实库执行（逐步手动，每步先征得用户同意）

024 只新增一张表，旧代码照常运行；新代码在没表时也只降级。先迁移、后重启。

#### 执行记录（2026-10-27，手动 psql 执行）

| 步骤 | 结果 |
| --- | --- |
| 1. 只读预检 | `data_fetch_runs` 不存在；`public` 表数 34；`schema_migrations` 最新仍为 `017_trade_plans.sql`；`job_reports` 无 `running` 行；服务进程 pid 83648 启动于 2026-10-26 15:36（v2.3 代码提交之前，`GET /api/data/families` 为 404） |
| 2. 全库备份 | `.local/us-workbench/backups/investment_assistant-before-024-20261027-100426.dump`（5,367,773 字节，sha256 `8c8e09dec0d5139fbfd6fa24180952381a0933d66c53093a334a64dd7533c244`）；`pg_restore --list` 可读，34 张表与线上一致 |
| 3. 执行 | 10:04:57 +0800，`psql --single-transaction -v ON_ERROR_STOP=1 -f migrations/024_data_fetch_runs.sql`（`CREATE TABLE`、`CREATE INDEX`）；`schema_migrations` 仍只到 `017` |
| 4. 验证 | `data_fetch_runs` 0 行，索引 `data_fetch_runs_pkey`、`idx_data_fetch_runs_family` 在，CHECK 约束 7 条；`public` 表数 34 → 35；`trade_plans` 8、`signal_candidates` 12、`pool_tiers` 30、`job_reports` 65、`scheduled_jobs` 3，与预检相同 |
| 5. 重启服务 | 10:05:19 +0800 `launchctl kickstart -k`，新进程 pid 96706 启动于 10:05:49；`/api/health`：数据库、调度线程（轮询运行中）、Yahoo、Discord、FRED up，SEC 首次探测为代理 SSL 中断、数秒后复查 up，3 项 `systemctl` 检查在 macOS 上为 down（与本次无关）；`/api/data/families`、`/api/data/families/bars`、`/api/data/browse/bars?ticker=MU`、`/api/workbench/today`、`/api/jobs/scheduled`、`/api/status` 均 200 |

手动验收（spec §11.2）见 [v2.3 验收记录](../archive/v2/roadmap/v2.3-acceptance.md)。

### 025 的真实库执行（逐步手动，每步先征得用户同意）

025 只新增一张表，旧代码照常运行；新代码在没表时也只降级（`stage_status.error`）。先迁移、后重启。

#### 执行记录（2026-10-27，手动 psql 执行）

| 步骤 | 结果 |
| --- | --- |
| 1. 只读预检 | `stage_runs` 不存在；`public` 表数 35；`schema_migrations` 最新仍为 `017_trade_plans.sql`；`job_reports` 无 `running` 行；服务进程 pid 6694 启动于 2026-10-27 10:47（v2.4 代码提交之前） |
| 2. 全库备份 | `.local/us-workbench/backups/investment_assistant-before-025-20261027-175527.dump`（5,372,345 字节，sha256 `1c46d0030a78bb0455add9f7277f38649224e2f0adef93e1c52b982533465f97`）；`pg_restore --list` 可读，35 张表与线上一致 |
| 3. 执行 | 17:55:36 +0800，`psql --single-transaction -v ON_ERROR_STOP=1 -f migrations/025_stage_runs.sql`（`CREATE TABLE`、`CREATE INDEX`）；`schema_migrations` 仍只到 `017` |
| 4. 验证 | `stage_runs` 0 行，索引 `stage_runs_pkey`、`stage_runs_run_id_stage_key`、`idx_stage_runs_session` 在，CHECK 约束 9 条；`public` 表数 35 → 36；`trade_plans` 8、`signal_candidates` 12、`job_reports` 65、`data_fetch_runs` 7、`scheduled_jobs` 3，与预检相同 |
| 5. 重启服务 | 17:55:42 +0800 `launchctl kickstart -k`，新进程 pid 97239 启动于 17:56:12；`/api/health`：数据库、调度线程（轮询运行中）、SEC、Discord、FRED up，Yahoo 首次探测为代理 SSL 中断、复查 up，3 项 `systemctl` 检查在 macOS 上为 down（与本次无关）；`/api/workbench/today`、`/api/workbench/stage-runs?session=2026-10-26`（`stage_runs_ready: true`）、`/api/data/families`、`/api/jobs/scheduled`、`/api/status` 均 200 |

### 026 的真实库执行（逐步手动，用户 2026-10-28 授权执行 v2.5 plan Task 14）

026 只新增表、列与放宽约束，对旧代码无害；新代码在缺表时降级为 `stages-1`。与 025 不同，**必须先迁移、后重启**（spec D19）。

#### 执行记录（2026-10-28，手动 psql 执行）

| 步骤 | 结果 |
| --- | --- |
| 1. 只读预检 | 16:30 +0800：`agent_plan_versions`、`plan_reviews`、`user_inputs` 都不存在，`signal_decisions` 无 `agent_plan_id` / `review_view`；`public` 表数 36；`job_reports` 无 `running` 行；服务进程 pid 97239 启动于 2026-10-27 17:56（v2.4 代码）；行数 `trade_plans` 9、`signal_candidates` 15、`agent_runs` 8、`agent_tool_calls` 72、`signal_decisions` 4、`stage_runs` 14、`job_reports` 69 |
| 2. 全库备份 | `.local/us-workbench/backups/investment_assistant-before-026-20261028-163044.dump`（5,582,037 字节，sha256 `e555371b5306ceec709b0fbb50920683186be8d1382ddf212ef7356118f3babf`）；`pg_restore --list` 可读，36 张表与线上一致 |
| 3. 执行 | 16:30:49 +0800，`psql --single-transaction -v ON_ERROR_STOP=1 -f migrations/026_plan_timeline.sql`（3 个 `CREATE TABLE`、4 个 `CREATE INDEX`、12 个 `ALTER TABLE`、3 个 `DO` 块、1 个 `DROP INDEX`）；三条 `NOTICE` 是首次执行时 `DROP CONSTRAINT IF EXISTS` 找不到具名约束，属预期；`schema_migrations` 仍只到 `017` |
| 4. 验证 | 三张新表各 0 行；`signal_decisions.agent_plan_id`（`uuid`）、`review_view`（`jsonb`）在；`agent_runs_mode_check` 允许 `propose`、`interpret`、`review`、`revise`，`agent_runs_mode_plan_check`、`agent_runs_interpret_no_pass_check` 与 spec §6.4 逐字一致；`agent_tool_calls_phase_check` 允许 `review`；`stage_runs_stage_check` 为 8 个阶段；`idx_trade_plans_agent_unique` 只管 `plan_version = 'agent-1'`；`public` 表数 36 → 39；旧表行数与预检相同 |
| 5. 重启服务 | 16:31:08 +0800 `launchctl kickstart -k`，新进程 pid 8923 启动于 16:31:38；`/api/health`：数据库、调度线程（轮询运行中）、Filings 存储、Discord、FRED up，SEC 与 Yahoo 经代理时有 SSL 中断（复查时交替 up / down，与 v2.4 验收日相同），3 项 `systemctl` 检查在 macOS 上为 down（与本次无关）；`/api/workbench/today`（`timeline_ready: true`）、`/api/jobs/scheduled`、`/api/status`、`/api/workbench/plan-reviews`、`/api/workbench/chains?ticker=QCOM`、`/api/workbench/user-inputs?ticker=QCOM` 均 200 |

回滚：`pg_restore --clean -d "$INVESTMENT_ASSISTANT_DATABASE_URL" <上面的 dump>`，再重启到 v2.4 代码。

### 027 的真实库执行（逐步手动，每一步单独授权）

027 只新增表并放宽三处约束，对 v2.6 代码无害；v2.7 代码在缺表时降级：批处理按 `stages-2` 记 8 行（没有 `retro` 行，`summary.errors` 有 `growth: migration_027_missing`）、`get_knowledge` 返回错误信封、新接口 503「迁移 027 未执行」、`today` / 链视图的 `knowledge_refs` 为空列表。与 026 一样**必须先迁移、后重启**（spec D29）：

1. **只读预检**：十一张表都不存在（`to_regclass` 为 null）；`job_reports` 没有 `running` 行；记下 `public` 表数与 `agent_runs`、`stage_runs` 的行数。
2. **全库备份**：`pg_dump -Fc "$INVESTMENT_ASSISTANT_DATABASE_URL" -f .local/us-workbench/backups/investment_assistant-before-027-<时间戳>.dump`，`shasum -a 256` 记下，`pg_restore --list` 确认可读。
3. **执行**：`psql "$INVESTMENT_ASSISTANT_DATABASE_URL" --single-transaction -v ON_ERROR_STOP=1 -f migrations/027_growth_loop.sql`；首次执行时 `DROP CONSTRAINT IF EXISTS` 找不到具名约束的 `NOTICE` 属预期；`schema_migrations` 仍只到 `017`。
4. **验证**：十一张新表各 0 行；`agent_runs_mode_check` 含 `retro`、`discuss`，`agent_runs_mode_plan_check`、`agent_runs_interpret_no_pass_check` 不变；`agent_tool_calls_phase_check` 含 `retro`、`discuss`；`stage_runs_stage_check` 为 9 个阶段；`public` 表数 +11；旧表行数与预检相同。
5. **重启服务**（[本机服务](local-service.md)）：新进程起来后手动跑一次批处理，`stage_runs` 该 `run_id` 有 9 行；`python -m investment_assistant.review.retro_cards check --since <首次批处理交易日 S 之前的第 4 个交易日>`（即 S−4，`retro.window_start(S)`：首次批处理只检测 S−4 到 S，起点再早会把窗口外的旧结束事件当成「缺卡」；要核对更早的事件，先 `retro_cards detect --since <日期>` 补卡再核对） 与 `python -m investment_assistant.review.knowledge.my_knowledge check-cutoff --since <上线日>` 通过。

回滚：`pg_restore --clean -d "$INVESTMENT_ASSISTANT_DATABASE_URL" <上面的 dump>`，再重启到 v2.6 代码。执行记录写进 `docs/roadmap/v2.7-acceptance.md` 的「迁移 027 执行记录」一节。

2026-10-29 已在真实库执行（13:39 备份、执行，13:40 重启），记录见 [v2.7 验收记录](../archive/v2/roadmap/v2.7-acceptance.md)。

### 028 的真实库执行（逐步手动，每一步单独授权）

028 改了调度行和 `stage_runs` 的唯一键，新旧代码混跑会产出无法守护的证据，所以**先迁移、后重启，两步之间只差一次重启**：

1. **确认状态**：当前库已到 027（`repository.growth_ready` 为真），服务仍在 v2.7 代码上运行。
2. **全库备份**：`pg_dump -Fc "$INVESTMENT_ASSISTANT_DATABASE_URL" -f .local/us-workbench/backups/investment_assistant-before-028-<时间戳>.dump`，`shasum -a 256` 记下，`pg_restore --list` 确认可读。**提醒**：028 之后库里有 `agent_connections`，其中的 ChatGPT 访问 / 刷新令牌是**明文**（与 `.env` 同一安全级别，决定 D29），所以之后的 `pg_dump` 备份文件同样含令牌——备份放 `.local/`（不入库），不要上传或共享。
3. **执行**：`psql "$INVESTMENT_ASSISTANT_DATABASE_URL" --single-transaction -v ON_ERROR_STOP=1 -f migrations/028_daily_workflow.sql`；首次执行时 `DROP CONSTRAINT IF EXISTS` 找不到具名约束的 `NOTICE` 属预期。
4. **核对**：三张新表存在，`stage_runs` 的 `attempt` 列与新约束在位，`scheduled_jobs` 只剩 `daily`（及其它非本版任务）且 `enabled` / `next_run_at` 合理。
5. **立即重启服务**到 v2.8 代码。

回滚：`pg_restore --clean -d "$INVESTMENT_ASSISTANT_DATABASE_URL" <上面的 dump>`，再重启到 v2.7 代码（回滚会丢掉 028 之后的所有写入，包括令牌）。执行记录写进 `docs/roadmap/v2.8-acceptance.md` 的「迁移 028 执行记录」一节。

#### 执行记录（2026-10-30，手动 psql 执行）

| 步骤 | 结果 |
| --- | --- |
| 1. 确认状态 | 09:36 +0800：`review_cards` 在（027 已执行），`agent_connections` 不存在；当天三个旧任务都已结束（`workbench` 06:30–06:47、`workbench_review` 08:00、`filings` 09:30–09:30:51，均 `success`），没有运行中的任务；服务进程 pid 16874（v2.7 代码）；`public` 表数 50，`job_reports` 77 行，`stage_runs` 48 行 |
| 2. 全库备份 | `.local/us-workbench/backups/investment_assistant-before-028-20261030-093702.dump`（6,793,707 字节，sha256 `09fde910f1e2c6853b2b49e8fb2490987a8d9d944db05365dae6f32c4a83f6f3`）；`pg_restore --list` 可读，50 张表与线上一致 |
| 3. 执行 | 09:37:12 +0800，`psql --single-transaction -v ON_ERROR_STOP=1 -f migrations/028_daily_workflow.sql`，退出码 0（12 个 `ALTER TABLE`、1 个 `DO` 块、3 个 `CREATE TABLE`、3 个 `CREATE INDEX`、`INSERT 0 1`、`DELETE 3`）；四条 `NOTICE` 是首次执行时 `DROP CONSTRAINT IF EXISTS` 找不到具名约束，属预期 |
| 4. 核对 | `scheduled_jobs` 只剩 `daily`：`11:30`、`2-6`、`Asia/Shanghai`、`enabled = true`（沿用原 `workbench` 行）、`next_run_at = 2026-10-30 11:30 +08`；`stage_runs` 有 `stage_runs_attempt_check`、`stage_runs_job_check`（`workbench`、`daily`）、`stage_runs_stage_check`（十个阶段）、`stage_runs_run_stage_attempt_key`，旧唯一键 `(run_id, stage)` 已删，48 行旧行全部 `attempt = 1`；`agent_connections`、`agent_connection_events`、`agent_attempt_resets` 各 0 行，`idx_job_reports_run_id` 在；`repository.daily_ready` 为真；`public` 表数 50 → 53，`job_reports`、`stage_runs` 行数不变 |
| 5. `.env` 与 plist | `.env` 删旧模型层的密钥变量（文件里本无 `WORKBENCH_AGENT_BASE_URL`、`WORKBENCH_AGENT_MODEL`），加 `WORKBENCH_AGENT_PROVIDER=chatgpt`；改前的 `.env` 另存于 `.local/us-workbench/backups/`（权限 600，含密钥，不入库）。LaunchAgent plist 的 `EnvironmentVariables` 只有代理变量与 `PATH`，没有这三个键，plist 未改 |
| 6. 重启服务 | 09:37:41 +0800 `launchctl kickstart -k`，新进程 pid 22589 启动于 09:38:11（v2.8 代码，`main@2e0bff2`）；`web/dist` 按 `main@2e0bff2` 重新构建（09:38）；`/api/health`：数据库、调度线程（轮询运行中）、Filings、SEC、Yahoo、Discord、FRED 均 up，3 项 `systemctl` 检查在 macOS 上为 down（与本版无关）；`/api/jobs/scheduled` 只有 `daily`（下次 2026-10-30 11:30 +08）与只手动的 `sec_backfill`；`/api/settings/agent-connection` 为 `disconnected`、`provider_setting = chatgpt`；`/api/workbench/today`、`/api/status`、`/api/jobs/reports?task=daily`、`/api/data/families` 均 200 |

**测试库**：`tests/conftest.WORKBENCH_MIGRATIONS` 已含 028（v2.8 收尾时加入），`tests/_daily_db.py` 的 `daily_db` 直接用这个 schema。


### 029 的真实库执行（逐步手动，每一步单独授权）

029 给 `trades` 与 `trade_plans` 加列、换掉一条价位约束并新建三张表；新代码在 029 之前拒绝运行 `daily`，所以**先迁移、后重启，两步之间只差一次重启**，并排在 v2.8 的手动验收（手动运行 `daily`、失败续跑演练）完成之后（spec D37）：

1. **确认状态**：当前库已到 028（`repository.daily_ready` 为真），服务在 v2.8 代码上运行，没有运行中的 `daily`。
2. **全库备份**：`pg_dump -Fc "$INVESTMENT_ASSISTANT_DATABASE_URL" -f .local/us-workbench/backups/investment_assistant-before-029-<时间戳>.dump`，`shasum -a 256` 记下，`pg_restore --list` 确认可读。备份含 `agent_connections` 里的明文令牌，放 `.local/`（不入库），不要上传或共享。
3. **执行**：`psql "$INVESTMENT_ASSISTANT_DATABASE_URL" --single-transaction -v ON_ERROR_STOP=1 -f migrations/029_executable_plans.sql`；首次执行时 `DROP CONSTRAINT IF EXISTS` 找不到具名约束的 `NOTICE` 属预期。
4. **核对**：`repository.executable_ready` 为真；`trades` 有 `plan_id` 与 `trades_link_one_check`；`trade_plans` 有 `entry_type`、`max_hold_sessions` 与新的 `trade_plans_levels_check`，旧行两列全为空；三张新表各 0 行；`plan_reviews` 的 `decided_by` 约束已放宽；旧表行数不变。
5. **重启服务**到 v2.9 代码，并核对 `web/dist` 是收尾 Task 之后构建的（`operations/local-service.md`）。

回滚：`pg_restore --clean -d "$INVESTMENT_ASSISTANT_DATABASE_URL" <上面的 dump>`，再重启到 v2.8 代码（回滚会丢掉 029 之后的所有写入，包括成交与备注）。执行记录写进 `docs/roadmap/v2.9-acceptance.md`（验收开始时新建）。

#### 执行记录（2026-10-30，手动 psql 执行）

| 步骤 | 结果 |
| --- | --- |
| 1. 确认状态 | 20:41 +0800：`agent_connections` 在（028 已执行），`chain_retriggers` 不存在；没有运行中的任务（最近一次是 11:30 的定时 `daily`，11:42 结束）；服务进程 pid 29512，10:33 启动（v2.8 代码，早于 v2.9 的第一个提交）；`public` 表数 53。v2.8 的手动 `daily` 与失败续跑演练已于同日上午完成（D37）。上线前只读核查见 [v2.9 验收记录](../archive/v2/roadmap/v2.9-acceptance.md) 第 1 节 |
| 2. 全库备份 | 20:42:02 +0800，`.local/us-workbench/backups/investment_assistant-before-029-20261030-204202.dump`（6,995,224 字节，sha256 `52db024683502ea558c6a54d488b4f40cbfa26c1431ba5639f7d20471703bd43`）；`pg_restore --list` 可读，53 张表与线上一致 |
| 3. 执行 | 20:42:17 +0800，`psql --single-transaction -v ON_ERROR_STOP=1 -f migrations/029_executable_plans.sql`，退出码 0（15 个 `ALTER TABLE`、2 个 `DO` 块、3 个 `CREATE TABLE`、3 个 `CREATE INDEX`）；六条 `NOTICE` 都是首次执行时 `DROP CONSTRAINT IF EXISTS` 找不到具名约束，属预期 |
| 4. 核对 | `repository.executable_ready` 与 `daily_ready` 为真；`trades` 有 `plan_id` 与 `trades_link_one_check`；`trade_plans` 有 `entry_type`、`max_hold_sessions`、`trade_plans_entry_type_check`、`trade_plans_max_hold_check`、`trade_plans_exec_fields_check` 与新的 `trade_plans_levels_check`（017 的旧价位约束已删，只剩这一条）；17 行旧计划两列全为空；`plan_reviews_agent_run_check` 允许 `decided_by = 'host'`；`chain_retriggers`、`holding_status`、`plan_notes` 各 0 行；`public` 表数 53 → 56；`trades` 0、`trade_plans` 17、`plan_reviews` 2、`signal_decisions` 4、`agent_plan_versions` 3、`job_reports` 84、`stage_runs` 105、`agent_runs` 23，与执行前一致 |
| 5. 重启服务 | `web/dist` 原是 18:55 的构建，早于发布提交 `c370103`（19:09），所以在 `main@676842b` 重新构建（20:42:43）；20:42:48 `launchctl kickstart -k`，新进程 pid 92196 启动于 20:43:18（v2.9 代码）。`/api/health` 与 v2.8 时一致：数据库、调度线程、Filings、SEC、Yahoo、Discord、FRED 均 up，3 项 `systemctl` 检查在 macOS 上为 down。`/api/jobs/scheduled` 只有 `daily`（下次 2026-10-31 11:30 +08）；`/api/settings/agent-connection` 为 `connected`（`gpt-6-astra`）；`/api/workbench/today`、`plan-table`、`holding-status`、`instruments/FER/plan-history`、`trades/linkable-plans`、`/api/data/families` 均 200；`POST /api/workbench/decisions` 返回 410 `decisions_retired`。`.env` 与 LaunchAgent plist 未改（本版无配置变化） |

**测试库**：`tests/conftest.WORKBENCH_MIGRATIONS` 已含 029。

### 030 的真实库执行（逐步手动，每一步单独授权；2026-11-01 已执行）

030 建六张新表、给四张账本表加 `account_id`（默认真实账户）并换掉 `holding_status` 的唯一键，还带本项目的第一行种子（默认真实账户）；v2.11 代码在 030 之前拒绝运行 `daily`、`/api/accounts/*` 返回 503，所以**先迁移、后重启，两步之间只差一次重启**（spec D22），排在 v2.10 的手动验收完成之后：

1. **确认状态**：当前库已到 029（`repository.executable_ready` 为真），服务在 v2.10 代码上运行，没有运行中的 `daily`。
2. **全库备份**：`pg_dump -Fc "$INVESTMENT_ASSISTANT_DATABASE_URL" -f .local/us-workbench/backups/investment_assistant-before-030-<时间戳>.dump`，`shasum -a 256` 记下，`pg_restore --list` 确认可读。备份含 `agent_connections` 里的明文令牌，放 `.local/`（不入库），不要上传或共享。
3. **执行**：`psql "$INVESTMENT_ASSISTANT_DATABASE_URL" --single-transaction -v ON_ERROR_STOP=1 -f migrations/030_accounts.sql`；首次执行时 `DROP CONSTRAINT IF EXISTS` 找不到具名约束的 `NOTICE` 属预期。
4. **核对**：`repository.accounts_ready` 为真；`accounts` 恰一行（默认真实账户，初始现金与开户日为空）；`trades` 有 `account_id`、`recorded_by`、`detail`，旧行 `account_id` 全为真实账户、`recorded_by` 全为 `user`；`holding_status` 的唯一键是 `(account_id, ticker, session)`；`agent_runs_portfolio_ticker_check`、放宽后的 `agent_runs_mode_check`（含 `portfolio`）、`agent_tool_calls_phase_check`（含 `portfolio`）、`stage_runs_stage_check`（11 个阶段，含 `accounts`）都在；六张新表除 `accounts` 外各 0 行；旧表行数不变。
5. **重启服务**到 v2.11 代码，并核对 `web/dist` 是收尾 Task 之后构建的（[本机服务](local-service.md)）。

回滚：`pg_restore --clean -d "$INVESTMENT_ASSISTANT_DATABASE_URL" <上面的 dump>`，再重启到 v2.10 代码（回滚会丢掉 030 之后的所有写入）。执行记录写进 `docs/roadmap/v2.11-acceptance.md`（验收开始时新建）。

#### 执行记录（2026-11-01，手动 psql 执行）

10:06:51 +0800 全库备份 `.local/us-workbench/backups/investment_assistant-before-030-20261101-100649.dump`（7,882,416 字节，sha256 `2b19b706…2b01`，`pg_restore --list` 可读，56 张表）；10:06:57 单事务执行，退出码 0，七条 `NOTICE` 属预期；核对全部通过（`accounts` 一行、四列默认值、`holding_status` 新唯一键、三处放宽、`paper_orders` 约束与索引，`public` 表数 56 → 62，旧表行数不变）；10:07 在 `main@d32a9a3` 重建 `web/dist`，10:08:06 重启到 v2.11 代码。逐项结果见 [v2.11 验收记录](../archive/v2/roadmap/v2.11-acceptance.md) 第 2、3 节。

**测试库**：`tests/conftest.WORKBENCH_MIGRATIONS` 已含 030（v2.11 Task 1 加入）；`tests/_daily_db.py::daily_db` 在此基础上执行 031，031 专项测试则从 001–030 建独立 schema。

### 031 的真实库执行（逐步单独授权；2026-11-03 已执行）

本节是执行前写下的步骤，031、roadmap 导入与重启已于 2026-11-03 在真实库完成，逐项记录见 [v2.12–v2.15 验收记录](../archive/v2/roadmap/v2.12-v2.15-acceptance.md) §1–§4 与 §6。当时整版代码已就绪（`stages-6` / `daily-3`、十三个节点、`agent-portfolio-2`、研究 / 路线 / 功能开关三页和 `/api/research`、`/api/roadmap`、`/api/settings/features` 等入口都在），但真实库还没有执行 031 和导入。当时的顺序是 **备份 → 031 → roadmap import → 重建 `web/dist` → 重启**：

1. **只读预检**：确认 030 就绪、没有运行中的 daily；记录旧表行数，核对历史 `price_bars.source` / `earnings_dates.source` 满足上表枚举。031 增加这两条 CHECK，不自动修正历史来源。
2. **全库备份**：`pg_dump -Fc "$INVESTMENT_ASSISTANT_DATABASE_URL" -f .local/us-workbench/backups/investment_assistant-before-031-<时间戳>.dump`；保存 SHA-256，`pg_restore --list` 确认可读。备份含明文 Agent 令牌，留在 `.local/`，不上传、不共享。
3. **单事务执行**：`psql "$INVESTMENT_ASSISTANT_DATABASE_URL" --single-transaction -v ON_ERROR_STOP=1 -f migrations/031_five_layers.sql`。旧 CHECK 按定义删除后以具名约束重建；首次执行的 `DROP CONSTRAINT IF EXISTS` 缺失 NOTICE 属预期。
4. **核对并导入**：`core.schema.five_layers_ready` 为真；十二张新表在导入前各 0 行、旧表行数不变，行业索引在位。B 方案保留 `agent_runs_mode_plan_check`、`agent_plan_versions.origin`、`agent_attempt_resets.mode`，研究不创建交易链。再运行 `python -m investment_assistant.optimize.roadmap import --dir config/roadmap`（`--dir` 必填；输出每个文件的 `{inserted, skipped}`），预期首次导入 `goals` 1、`versions` 17、`decisions` 124、`evidence` 11 全部为 inserted；立刻再跑一次，四项应全部为 skipped、0 条 inserted（同键同内容跳过，不同内容报冲突且整次不写入）。再核对导入记录与原文，页面条数与 `export` 导出一致。
5. **重建前端并重启**：本版新增研究、路线、功能开关三页，必须先 `cd web && npm run build` 重建 `web/dist`，并核对构建时间晚于发布提交（漏了这一步会重启到旧前端，三页都没有）；再单独授权重启到整版代码（[本机服务](local-service.md)），确认 daily 的 031 门通过，再按发布验收步骤操作；这里不提前触发任务或通知。

八张只追加表不得 UPDATE / DELETE。`data_requests` 只能从 queued 结算一次；后续只可为 fetched 且 rows>0 的行补空的 `rerun_run_id`。`roadmap_proposals` 仅从 proposed 决定一次；`portfolio_checks` 仅当 commentary 为 NULL 时补一次评论与 pm_run_id，不改硬结果。按 D51，`research_requests` 可多次合法转移，但每次必须匹配期望前态；`research_versions` 仍只追加。

031 未就绪时，run 返回 error；resume / 手动 ensure_ready 抛 `migration_031_missing`（HTTP 503），三者都尝试追加一条 error 报告。ensure_ready 只是预检，不发送失败通知；实际 run / resume 沿用失败通知路径。数据库不可写时只能记录日志，不保证报告入库。

证据计账：`data_fetch_runs.detail.targets` 仍最多保留 600 个目标；`detail.source_counts` 在截断前按每个目标的**最终成功结果**统计来源（重试 / 覆盖只算最后一次，失败备源不计成功）。`source_counts_complete` 仅在全部成功目标都有非空来源时为 true；旧调用未提供来源时不猜成主源。备源 SQL 的 `backup_used` 只计 stooq / nasdaq 成功目标；新记录优先用全量来源汇总，旧记录只用仍保留的成功来源证据。`backup_incomplete_runs` 按族计来源证据不完整的运行数（每次运行最多一次）：旧截断记录或成功来源覆盖不足时，`backup_used` 只能解释为已知下界，不能把零称作「没有用过备源」。不回填历史证据，也不据此启用任何备源适配器。

roadmap 待验收天数按版本顺序中的**当前连续 pending 时期**计算，同状态修正文案不会重置起点；离开 `user_acceptance_pending` 后再进入才重新起算。仍返回最新版本字段，严格超过 30 天才进入默认逾期证据（保留小数天）。

回滚须另行授权：停服务、按本页恢复备份，再重启到兼容的旧代码；恢复会丢掉备份后的所有写入（包括开关、研究与 roadmap 记录）。

### 033 在真实库的执行步骤（待授权）

**状态：已在一次性库验证，真实库尚未执行。** 执行需要用户单独授权，下面是准备好的步骤，不是执行记录。

**删表保护**：`migrate` 在 `033_drop_v2_tables.sql` 待执行、且它要删的表里至少有一张存在并且有行时，会拒绝执行并退出，一条迁移都不应用（包括排在 033 前后的其他待执行迁移）；设置环境变量 `INVESTMENT_ASSISTANT_ALLOW_DROP_V2_TABLES=1` 才确认执行。空库、全新建库（那些表不存在或都是空的）不受影响，不需要这个变量。注意迁移 007、008、013、016、020、028、030 会往 `scheduled_jobs`、`notify_settings`、`accounts` 里写入种子行，所以任何已经应用到 032 的库（哪怕没有业务数据）都会被拒绝，需要带上这个变量。所以在完成 `pg_dump` 并得到授权之前，对真实库运行 `migrate` 会被拒绝，这是预期行为。**033 没执行之前，034 及以后的迁移也无法对真实库执行**（整个 `migrate` 被拒），后续步骤若需要新迁移，要先完成 033 的备份与授权。

**影响**：033 删除 57 张 v2 业务表和 LangGraph 的四张检查点表，其中有 `agent_connections`（含旧的 ChatGPT OAuth 登录态与令牌）和账户、成交、计划、复盘等全部 v2 数据。**执行后不可恢复，只能从备份还原。** 保留的表有 19 张：11 张事实表（日线、资讯与资讯标的、申报、财报日、宏观、行业归类、美股基本面观察与采集状态、拉取记录、关注列表）、`feature_flag_changes`（`core.features.current` 仍读它）和七张 `v3_` 表。完整清单以 [`migrations/033_drop_v2_tables.sql`](../../migrations/033_drop_v2_tables.sql) 的 `DROP TABLE` 语句和 [`tests/test_migration_033.py`](../../tests/test_migration_033.py) 的 `DROPPED`、`KEPT_FACT_TABLES` 为准。

1. **只读预检**：确认没有正在跑的 `facts run`；`schema_migrations` 最新一条是 `032_v3_agent_first.sql`；记下保留表的行数，执行后对照。

   ```bash
   psql "$INVESTMENT_ASSISTANT_DATABASE_URL" -Atc "SELECT max(filename) FROM schema_migrations"
   for t in price_bars news_items news_item_tickers filings earnings_dates macro_observations sector_classifications \
            us_research_observations us_research_collection_state data_fetch_runs watchlist_items feature_flag_changes \
            v3_runs v3_candidates v3_verdicts v3_plans v3_trades v3_equity v3_tool_calls; do
     psql "$INVESTMENT_ASSISTANT_DATABASE_URL" -Atc "SELECT '$t', count(*) FROM $t"
   done
   ```

2. **全库备份**（照 032 的先例放 `.local/v3/`）：

   ```bash
   pg_dump -Fc "$INVESTMENT_ASSISTANT_DATABASE_URL" -f .local/v3/backup-before-033.dump
   shasum -a 256 .local/v3/backup-before-033.dump
   pg_restore --list .local/v3/backup-before-033.dump | head
   ```

   备份含 `agent_connections` 里的明文令牌，留在 `.local/`（不入库），不要上传或共享。

3. **执行**（照 032 的先例走 `migrate` 入口；它只会应用 033，并记入 `schema_migrations`）：

   ```bash
   INVESTMENT_ASSISTANT_ALLOW_DROP_V2_TABLES=1 INVESTMENT_ASSISTANT_DATABASE_URL=... .venv/bin/python -m investment_assistant.migrate
   ```

   不带 `INVESTMENT_ASSISTANT_ALLOW_DROP_V2_TABLES=1` 时，真实库上会被删表保护拒绝。预期输出 `{"applied": ["033_drop_v2_tables.sql"], "count": 1}`。迁移文件自带 `BEGIN` / `COMMIT`，整个迁移是一个事务；失败会整体回滚。

4. **核对**：被删的表用 `to_regclass` 逐张查，应全部为空（表已不存在）；保留的表都在，行数与预检一致。

   ```bash
   for t in $(sed -n 's/^DROP TABLE IF EXISTS \([a-z_0-9]*\);$/\1/p' migrations/033_drop_v2_tables.sql); do
     psql "$INVESTMENT_ASSISTANT_DATABASE_URL" -Atc "SELECT '$t', to_regclass('$t')"
   done
   ```

   再重复第 1 步里的行数循环，与预检逐张对照；最后 `.venv/bin/python -m investment_assistant.facts show` 应照常打印最新草稿。

回滚：`pg_restore --clean --if-exists -d "$INVESTMENT_ASSISTANT_DATABASE_URL" .local/v3/backup-before-033.dump`（会丢掉备份之后的所有写入，包括之后的 `facts run` 记录）。执行后把结果（备份大小与 sha256、`applied` 输出、核对结论）补记到本节。

## 一次性测试库

数据库测试只在设置了 `INVESTMENT_ASSISTANT_TEST_DATABASE_URL` 时运行，每个测试在独立 schema 中建表、结束后删除。永远不要把它指向真实库。夹具（`tests/conftest.py`）把迁移应用到 033 为止，所以保留的数据库测试都在已删掉 v2 表的 schema 上跑。

```bash
initdb -D <临时目录>/pg/data -U test -A trust --no-locale -E UTF8
LC_ALL=C pg_ctl -D <临时目录>/pg/data -o "-p 55433 -k '' -c listen_addresses=127.0.0.1" start
createdb -h 127.0.0.1 -p 55433 -U test workbench_test
INVESTMENT_ASSISTANT_TEST_DATABASE_URL=postgresql://test@127.0.0.1:55433/workbench_test \
  env -u INVESTMENT_ASSISTANT_DATABASE_URL .venv/bin/python -m pytest tests/ -q
```

Unix socket 路径太长时启动会失败，所以用 `-k ''` 关掉 socket、只走 TCP；不加 `LC_ALL=C` 时服务会拒绝启动。
