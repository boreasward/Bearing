# v3 事实任务与工具面（v3 spec §15.6 第 1 步）

v3 第 1 步交付两样东西：每天跑一次的**事实任务**（采集八族数据 → 规则算名单 → 落一条运行记录和一份草稿文件，全程不调模型），
和给模型用的 **MCP 工具面**（`toolsurface/`，stdio JSON-RPC，brief / chat 两种模式）。本文是这两样的运维说明，
末尾附 2026-11-06 在真实库上的迁移 032 执行记录、各次试跑的实测耗时（含成功路径）与真实客户端冒烟的结论。

## 事实任务：两条命令

```bash
.venv/bin/python -m investment_assistant.facts run    # 跑一次：先采集，再算规则名单，写运行记录与草稿
.venv/bin/python -m investment_assistant.facts show   # 打印草稿（数据库里最新一条运行的）
```

- `run` 可加 `--no-collect`（跳过采集，直接用库里已有的数据算）与 `--trigger scheduled|manual_batch`
  （写进运行记录与 `data_fetch_runs`，缺省 `manual_batch`）。
- `show` 可加 `--session YYYY-MM-DD`；不给日期则取最新一条运行。
- `run` 的 stdout 是一行 JSON（`run_id`、`session`、`status`、`error`、`candidates` 条数、
  `draft_path`、`review`——当天的计划复查结果，`{'changes': […事件…], 'unchecked': [缺日线的持仓],
  'errors': [复查失败的计划及原因]}`；一份计划的复查失败只回滚它自己，不中断运行）。

退出码（两个子命令同一套）：

| 退出码 | 含义 |
| --- | --- |
| 0 | `run` 的运行记录 `status=ok`；`show` 打印出草稿 |
| 1 | `run`：运行记录 `status=error`（目前唯一来源是基准日线缺失，见下）；`show`：查无运行记录 |
| 2 | 数据库不可用（stderr 有原因，不写任何记录） |

`v3_runs` 只追加：重复跑产生新的运行记录；草稿文件按交易日命名，同一交易日最新的运行覆盖文件内容。

## 草稿文件在哪

按优先级取三处之一：配置键 `v3.briefs_dir`（见下）→ 环境变量 `INVESTMENT_ASSISTANT_RUNTIME_BASE` 下的
`briefs/` → 都没设时仓库本地 `.local/v3/briefs/`。本机 `.env` 设了 `INVESTMENT_ASSISTANT_RUNTIME_BASE`，
草稿落在 `~/Library/Application Support/investment-assistant/briefs/<交易日>.draft.md`。

正常草稿是 v3.1 十段简报去掉 agent 部分的宿主版：今日操作（没有就不出现）、和昨天比变了什么、
市场环境、持仓（含止损与下一交易日的操作）、待入场计划、规则名单（含连续天数与掉出名单）、
账户、今天下不了结论的、页脚；标题下写明「二次确认未完成」。基准（SPY）当日日线缺失时，
运行记录 `status=error`、退出码 1、无 traceback，草稿只剩标题、缺失原因与页脚；此时若有持仓，
第一行写「今天无法检查止损」（这是设计好的错误路径，不是崩溃）。

## "v3" 配置键与默认值

配置文件路径由 `INVESTMENT_ASSISTANT_CONFIG` 指定；`v3` 一节的键全部可选，缺省即 spec 冻结值：

| 键 | 默认 | 含义 |
| --- | --- | --- |
| `top_n` | 5 | 二次确认取名单前几只 |
| `collect_workers` | 6 | yfinance 系族（bars / news / earnings）的采集并发数 |
| `sec_workers` | 3 | SEC 系族（sec_filings / fundamentals / sector）的采集并发数 |
| `replay_sessions` | 20 | 形态因子回放的信号日个数 |
| `hold_sessions` | 5 | 每次触发的固定持有交易日数（回放窗口整体提前这么多天，保证每个样本结果完整） |
| `min_triggers` | 10 | 买入规则触发次数不足此值的，形态档位记 `insufficient` |
| `win_hi` / `win_lo` | 0.60 / 0.40 | 胜率上下限：胜率 ≥ `win_hi` 且平均收益为正记 `favorable`，胜率 ≤ `win_lo` 或平均收益为负记 `unfavorable`，其余 `neutral` |
| `min_group` | 3 | 行业组参与强度排序的最小样本数，不足记 `insufficient` |
| `sector_etfs` | QQQ、SMH、XLK、XLV、XLY、XLP、XLE、XLF、XLI、XLB、XLU、XLRE、XLC | 行业 ETF 集合 |
| `briefs_dir` | 空 | 草稿目录覆盖；空则按上面的优先级取 |

采集并发的实现是 `data.registry.run_adapter` 新增的 `workers` 参数：缺省 1，与改动前的逐个拉取完全等价，
旧调用不受影响；只有事实任务按 `collect_workers` / `sec_workers` 传入。

## 并行期：与旧系统共用事实表

并行期已于 2026-11-06 结束，旧系统已移除（见 [roadmap](../roadmap.md) 第 5 步）；以下内容是并行期的记录。

旧系统（v2.15）在第 5 步下线前照常运行，v3 与它共用：

- **事实表**（日线、财报日、行业、宏观、资讯、申报列表、基本面）：两套系统各自的采集都往里追加新数据，不改不删旧行；
- **`data_fetch_runs`**：v3 的每族一行，`job = 'v3'`，`trigger` 为 `scheduled` / `manual_batch`；
- **七张 `v3_` 表**（`v3_runs` / `v3_candidates` / `v3_verdicts` / `v3_plans` / `v3_trades` / `v3_equity` / `v3_tool_calls`）：v3 专用，只追加，旧系统不读不写。

并发保护沿用族级 advisory 锁：v3 的采集与旧系统的采集同族不会同时拉取，后到的等先到的。旧系统每日任务的
运行窗口（北京时间 11:30–12:10）内不要手动跑 `facts run`。

## 工具面：怎么起、两个运维要求

一个进程服务一个交易日、一种模式，stdio JSON-RPC：

```bash
.venv/bin/python -m investment_assistant.toolsurface \
  --session 2026-11-05 --mode brief --agent-session <会话编号> [--env-file .env]
```

- 31 个工具：21 个两种模式都有（读、算计划价位、保存结论与计划、计划线的延续 / 修订 / 持仓复查 /
  一句话总结）；`record_trade`、`record_equity`（对账）、`record_cash_flow`、`confirm_stop_order`、
  `raise_stop`、`cancel_plan`、`void_record`、`watchlist_add`、`watchlist_remove`、`ensure_data`
  十个只在 `chat` 模式可用。
  - v3.1 新增：`get_account`（账户视图）、`get_agenda`（当天待办）、`continue_plan`、`revise_plan`、
    `save_holding_review`、`save_summary`、`record_cash_flow`、`confirm_stop_order`、`raise_stop`、
    `cancel_plan`、`void_record`、`watchlist_add`、`watchlist_remove`。
  - v3.1 改动：`get_candidates` 每只加 `streak` / `has_active_plan` / `plan_id`，返回加 `dropped`；
    `get_positions` 换成计划线口径的持仓行；`get_plans` 改按状态 / 股票 / 计划编号过滤，每份带变化记录；
    `save_plan` 增加已有有效计划、名额与替换校验；`save_verdict` 的 veto 会作废待入场计划；
    `record_equity` 语义改为对账（返回差额）；`record_trade` 按计划线推进（买入返回止损单指令，
    卖完返回平仓结果）。
  - 计划是跨天的一条线：`v3_plan_events` 里每次变化一行；带 `request_id` 的写工具先复述、
    `confirmed` 为真才写、按 `request_id` 幂等。
- 交易日与数据截止时点在进程启动时固定，模式与会话编号由宿主传入，模型改不了；每次调用（含出错的）
  写一行 `v3_tool_calls`（`agent_session`、`mode`、参数、结果摘要）。
- 提供方与模型 id 只由宿主以命令行参数传入，不进任何仓库文件（做法见 [pi-unattended.md](pi-unattended.md)）。

实测得到的两条要求：

1. **`.pi/mcp.json` 的服务条目要加 `"cwd": <仓库根目录>`**：服务以 `python -m investment_assistant.toolsurface`
   启动，包必须可导入；venv 不把它 pip 安装进环境，靠 cwd 落在仓库根。
2. **服务指向的库必须有事实表，不能只有 032 的 `v3_` 表**：`core.tx` 在服务请求前会探测 7 张事实表
   （`core.schema.WORKBENCH_TABLES`：`price_bars`、`news_items`、`news_item_tickers`、`filings`、`earnings_dates`、
   `watchlist_items`、`macro_observations`，另查 `filings.accepted_at` 列），缺任意一张就判「数据库不可用」。这些表由迁移
   002、011、016、017、018 建出，按完整迁移链（001–033）建库即可；033 之后这 7 张表仍在。

## 迁移 032 的执行记录（2026-11-06，真实库）

- **备份**：任何改动之前先 `pg_dump -Fc` 全库备份到 `.local/v3/backup-before-032.dump`（约 15 MB；
  含明文令牌，留在 `.local/` 不入库、不上传、不共享）。
- **执行**：`.venv/bin/python -m investment_assistant.migrate` 输出
  `{"applied": ["032_v3_agent_first.sql"], "count": 1}`——只新建七张 `v3_` 表，不动任何旧表。
- **事件（如实记录）**：真实库的 `schema_migrations` 追踪表只记到 017，而物理 schema 早已是 v2.15 的最终
  状态（020–031 的表、约束、种子都在——v2.15 当时按惯例手动 psql 执行、从未写追踪行）。因此第一次
  `migrate` 先重放了 018 / 019（无害：`IF NOT EXISTS` 与同定义约束），随后在 020 的中间态约束上对
  合法存量数据失败（4 行 `mode='revise'` 的运行记录在 020 口径下不合法、在最终口径下合法，是旧系统
  11 月 4–5 日正常写入的），该事务整体干净回滚，零损伤、已逐项核对。
- **处理**：先做 020–031 最终状态的逐对象核对（44 张表、36 个索引、约束与种子全部一致），经用户授权把
  12 条追踪行（020–031）补进 `schema_migrations`，再跑 `migrate` 就恰好只应用了 032。此后真实库的追踪
  表与物理 schema 一致，`migrate` 入口可继续安全使用（[migrations-and-backup.md](migrations-and-backup.md)
  里「真实库从 018 起不走 migrate 入口」的告诫针对的就是这种不一致，已按上面的方式消除）。

## 真实库试跑记录（2026-11-06 上午，北京时间）

用户授权、真实网络、避开旧系统运行窗口，共三次 `facts run`：

| run_id | 耗时 | 结果 |
| --- | --- | --- |
| `v3-20261106013702` | 2 分 20 秒 | `status=error`：基准日线缺失：SPY: missing_signal_bar，退出码 1 |
| `v3-20261106014008` | 2 分 24 秒 | 同上 |
| `v3-20261106014836` | 3 分 04 秒 | 同上 |

- **采集本身全部成功**：bars 116/116 目标全部拿到；universe / earnings / sector 各 102–116；
  fred / news / sec_filings / fundamentals 全部 success。八族全量采集加前五只补采合计
  2 分 20 秒–3 分 04 秒，远低于 5 分钟目标（`collect_workers=6`、`sec_workers=3` 的实测口径）。
- **失败不在采集**：三次运行时数据源都还没有 2026-11-05 的日线（任何标的都没有；当时旧系统 11:30 的
  任务也还没跑，三次一致），规则链的第一条前置（SPY 当日必须有 bar）不成立，走的是上面的错误路径。
### 成功路径（2026-11-06 12:12，北京时间）

旧系统当天 11:30 的任务 11:48 跑完、数据源有了 2026-11-05 的日线之后，用户授权再跑一次：

| run_id | 耗时 | 结果 |
| --- | --- | --- |
| `v3-20261106041233-6f728de5` | 3 分 20 秒（含采集） | `status=ok`，交易日 2026-11-05，名单 20 只，退出码 0 |
| `v3-20261106041612-d9b8031a` | 约 1 秒（`--no-collect`） | `status=ok`，名单 20 只 |
| `v3-20261106041613-290eba3e` | 约 1 秒（`--no-collect`） | `status=ok`，名单 20 只 |

- `show` 打印出五个小节齐全的草稿；名单每行带规则理由，前 5 只（KDP、COST、EXC、FER、PDD）标「进入二次确认」；
  覆盖一节列出缺失项（日线缺 NXPI，财报日缺 IDXX、TXN，基本面缺 FER），全文没有「降级」。
- 三次运行的 `v3_candidates` 在 (ticker, rank, reason) 上完全一致（spec 验收 5）。
- 含采集的总耗时 3 分 20 秒，在 5 分钟目标内（spec 验收 1）。

## 冒烟记录（2026-11-06，真实 Pi 客户端）

- **一次性库**：真实 Pi 客户端（1.5.0）只拿到 `mcp__ia__get_candidates` 一个工具，调用后回复了种子里排名第一的
  代码 NVDA，`v3_tool_calls` 记下这次调用（`agent_session='smoke'`）。过程中确认了上面「工具面」一节的两条运维要求。
- **真实库**（成功路径之后）：同样的命令行指向真实库、交易日 2026-11-05、`brief` 模式，11 秒完成，只出现
  `mcp__ia__get_candidates` 一个工具事件，`stopReason=stop`，回复 `KDP`（与草稿第 1 名一致），
  `v3_tool_calls` 有 `agent_session='pi-smoke-20261106'` 的一行。另用 stdio 直接调了 `tools/list`（brief 15 个、
  不含 chat 专用工具）、`get_candidates`、`compute_plan_levels`（未记本金，`sizing.status=no_equity`）和一次非法代码
  （返回 `invalid_ticker` 错误信封，服务继续处理），均符合预期。
- 起服务的 shell 若是 zsh，转发多个代理变量时要按空格拆开（`${=VAR}`），否则 Pi 会把它们当成一个代理地址而启动失败。
