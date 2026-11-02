# 数据菜单：数据族状态与原始数据

v2.3.0 新增：让每类数据看得见。六个数据族（日线、SEC 申报、资讯、ATR、FRED 利差、纳指 100 范围）的用途、覆盖、最新时点、最近拉取成败在「数据」菜单可见，可单族手动拉取，可浏览原始值。设计见[数据基础 spec](../specs/2026-10-26-v2.3-data-foundation-design.md)。

## v2.12 注册表：九族与指标卡（随 v2.15.0 发布，待使用验收）

九族、真实主备、补查与 22 项标准指标的产品说明见 [数据注册表](data-registry.md)，验收条目见 [验收清单 §1](../roadmap/v2.12-v2.15-acceptance.md)。

- 数据层的唯一注册项依序为 `bars / news / sec_filings / earnings / fred / universe / fundamentals / sec_text / sector`。覆盖、最新时点与手动采集由真实 Adapter 提供；手动采集冻结一次开关和依赖，保持族锁、独立事务与追加记录。目标先由宿主提供池 / 持仓 / 活跃链事实，再由适配器统一集合；单只目标不扩成全池，池或范围文件降级仍给说明。
- **未迁移 031 的兼容性**：原五个数据族和 ATR 只读投影仍可用（六张卡，含 ATR 派生卡）；不访问尚不存在的开关 / 研究请求 / 行业表。旧族手动拉取显式使用 `pre_031_primary_only` 上下文，备源不开启，这是 schema 能力边界，不是用户关闭了开关。新增四族手动 / 详情 / 浏览与指标卡在外部工作前返回 503「需执行迁移 031」。031 就绪后，总览返回九张适配器卡（按注册顺序）加一张「标准指标」卡，读取真实默认开启的开关；`atr` 卡由指标卡取代（旧 ATR 详情 / 浏览保留为视图投影，不是数据族）。
- **卡片来源**（v2.12，spec §5.6）：每张适配器卡带 `sources`（`[{id, note}]`，主源在前）与 `today_sources`（`{primary, backup, failed}`：本交易日最新一条整族运行里，最终取自主源 / 备源的目标数与什么也没拿到的目标数；来自 Tally 的 `detail.source_counts`，旧行没有该字段时成功数全算主源；本交易日没有整族运行、单只探测或已跳过的运行都计 0）。族详情的目标行多 `source_used` 与 `fallback`（该目标最近一次记录的来源与 `{tried, reason}`，没有记录为 `null`）；日线浏览多 `source` 列。指标卡 `id='indicators'`、`kind='derived'`，`items` 逐项列 `id / title / basis / window / used_by / concept`，`GET /api/data/indicators` 给同一清单（`{version, items}`，不读库）。
- **新增四族的原始浏览**（窗口固定，只读已落库且当时可见的内容，不采集）：`earnings` 最近 20 条财报日答案（日期、状态、来源、采集时间）；`fundamentals` 最近 8 个财季的 `metric / value / unit / period / form / filed`（季度 = 60–120 天的区间事实，时点事实取季末日；来源冲突的事实无值并标 `conflict`）；`sec_text` 每份已下载原文（表单、申报日、下载 / 接受时间、items）附正文分析摘要（`analyzed`、覆盖度、抽出的事实 `display / period`、文档数、警告）；`sector` 行业答案历史（`sic / sic_description / group / source / fetched_at`，答案变化才追加）。均需要 `ticker`，缺 ticker 400，未采过的标的为空结果。
- bars 正式只有 yfinance；Stooq spike 不可用，不启用。备源补缺机制只写近十个交易日中完全缺行的日期，以对应缺日之前最近主源 `adj_close / close` 调整，已有日期不追加备源修订。财报日备源为 Nasdaq；主备真实尝试次数合并，双失败仍保留主源原因。
- SEC 正文适配器用有效配置 `filings.output_dir`，`.budgets` 与正文同根持久保留，**不能在两次运行间清理**。每只每 session 最多预留三次原文下载（主文与 EX-99.1 附件共用）；下载前扣额，失败不释放；发现或下载超时后同 session 跨入口不再请求，次 session 可重试。宿主同时传入 `sec_monitor.state_dir` 读取已验证附件，缺失附件 / 预算不足不发布残缺分析；冻结 `now` 先过滤接受时间再选择与下载，未知接受时间保持未知。安全范围内的缺失索引原文原位恢复；Tally 的新增数是实际观察行 / 修订，不是下载数。SEC 监控节点的下载与研究刷新已改接该适配器（`scope='sec_monitor'`，其 `data_fetch_runs` 行的族是 `sec_text`、`detail.scope='sec_monitor'`；每族每 scope 一行，数据菜单按族汇总），日批 `collect` 的 `sec_text` 行覆盖其余目标并跳过本次已处理的监控目标。
- `Tally` 的 `source_counts / source_counts_complete` 在 600 项明细截断前按所有目标末次成功结果统计。SQL→提案证据保留 `backup_incomplete_runs`；历史缺来源或截断时，已知备源使用数只是下界，不能解读为准确零。每日 `collect` 节点、筛选新成员补采（`scope = 'screen_new_members'`）、SEC 监控与回补（`sec_text`）和数据菜单手动拉取都走同一个 `run_adapter`（主源 → 备源 → 降级，按运行冻结的 `backup_sources`），旧的逐族拉取函数和 `refresh_local_filings` 已删除。命令行 `python -m investment_assistant.data.registry list / fetch <family> [--ticker T]` 与菜单同一路径（逐只族必须给 `--ticker`）。

- 每族**每 scope** 记行，数据菜单按族汇总、按 scope 展开。不把未知 SIC 当拦截，不把旧记录没有来源当主源成功。

下文「六张卡」、ATR 浏览与旧拉取范围描述的是 v2.3–v2.11 的页面口径（迁移 031 未执行的库仍是这个样子）；031 就绪后的九族 + 指标卡以上面各条和 [数据注册表](data-registry.md) 为准，验收不复用下文旧六卡条数。

## 做什么

- **六张卡**：每个数据族一张——用途、类型（采集 / 派生 / 手动维护）、覆盖 `covered / targets`（滞后目标点名，附各族覆盖口径的一句话说明）、最新时点、最近一次**全族**拉取（状态、触发来源、实际任务路径、成败计数、失败原因前 5 条、skipped 原因）；比它更新的单只拉取另起一行「最近单只拉取」（标的、状态、时间、失败原因），下一次全族拉取后自动收起，单只试探不会冒充整族状态；最近一次成功时间（只算全族记录）、所属任务（开关与下次运行；仍是注册任务，如 SEC 申报卡为 `filings`——注意 `workbench` 批处理也会按「当前池 + 持仓」采集 SEC 申报列表，两路目标不同，见拉取记录的 `job` 与 `targets_source`）。覆盖与最新时点在读取时从数据表实算，不另存状态。
- **只追加的拉取记录**：每次拉取（定时批处理、手动批处理、数据菜单手动拉取、`filings` 任务）向 `data_fetch_runs` 追加一行，记录成败与逐只原因；失败原因截断 300 字，并去掉 `FRED_API_KEY` 等凭据。整次调用失败记一条 `{"target": "*"}` 哨兵，`*` 不是目标、不进分母，只作用于该行自己的范围。
- **外部请求策略 `fetch-1`（v2.8）**：所有外部请求走 `data/fetch_policy.py`：连接 10 秒、总共 30 秒，可重试的失败（超时、连接错误、`429`、`500` / `502` / `503` / `504`）再试 2 次（隔 2 秒、8 秒），`4xx`、空结果、解析失败不重试；Yahoo（yfinance）用自建的 `curl_cffi` 会话并外加 35 秒线程截止，建立共享会话时关掉 yfinance 的 `hide_exceptions`，库里的异常原样抛出：代理或连接失败按 `network` / `timeout` 重试，Yahoo 明确答「无数据 / 无时区」（`YFPricesMissingError` / `YFTzMissingError`）才由日线那一路记成 `empty`（之前两者都是空表、都算 `empty` 不重试，2026-10-31 的 `benchmark_missing` 就是代理抖动被当成了 QQQ 无数据）；财报日的 `4xx` / `5xx` 与资讯搜索的解析失败也因此成为该目标的失败而不是空结果；推送只在请求未发出与 `429` 时重试。单个目标最坏约 100 秒（`requests` 一路每次尝试的连接、响应头与正文共用 30 秒的硬截止）；正文读到一半的超时或断连同样按 `timeout` / `network` 重试；错误文本里不出现 URL 与 webhook 令牌。共用同一个进程级 yfinance 会话。概念卡：`system.fetch_policy`。
- **目标级明细（`detail.targets`）**：每个数据族的拉取记录（`data_fetch_runs.detail`）新增 `targets`，每个目标一项 `{target, ok, attempts, error_kind, http_status, reason, rows, latest, elapsed_ms}`，按目标名排序，一族至多 600 项（超出记 `detail.targets_truncated` = 被截掉的条数），并带 `detail.policy = 'fetch-1'`。`rows`：日线 = 本次新写入行数、资讯 = 新写入条数、SEC = 发现的申报数、FRED = 新写入观测数；`latest`：日线最新 `trade_date`、资讯最新发布时间、SEC 最新申报日、FRED 最新观测日。`failures` 列照旧只列失败项，族状态的判定没有变。SEC 申报列表改为逐只采集（一只缺 CIK 或断连只失败它自己，整族 `*` 失败只在 `company_tickers.json` 都取不到时出现）。旧记录没有 `targets`，页面只按 `failures` 显示失败项、成功项只显示计数。
- **单族手动拉取**：共用批处理的同一组采集函数，可全族或单只（`bars`、`news`、`sec_filings` 支持单只）；不跑规则、不生成计划或候选、不改池、不推送。同族并发用 advisory lock 互斥：正在采集时接口返回 409。提交前会先检查数据库可用、工作台旧表与迁移 024，缺任一返回 503 且不启动后台任务；`ticker` 必须是字符串，否则 400。
- **原始值浏览**（窗口固定）：日线最近 120 个交易日，附每日修订次数，OHLC 拆股调整、未分红调整，`adj_close` 全调整；ATR 现算最近 60 个（与 `plan-1` 同一函数，不入库）；资讯近 60 天（窗口内完整读取，SQL 先按来源过滤，没有第二个静默条数上限）；SEC 申报近 180 天（`filings` 行 + 未入库的 `sec_8k`；无接收时间的条目标注「申报日未知」，不把采集时间当申报日期）；FRED 每条序列最近 120 个观测；范围文件的元数据与成员表。
- **证据文案常驻**：「这里只展示采集事实。所有规则都未经验证（Phase 0 Gate HOLD）：数据完整不代表信号有效。」；ATR 卡与 ATR 浏览另标「`plan-1` 的计算输入，规则 `gate_hold`」。页面顶部显示一次，卡片和浏览区不再逐张重复，只显示 ATR 这句补充。后端文案里用反引号标的标识符（如 `plan-1`）按代码样式显示，不显示反引号本身。

## 页面

- **总览 `#data`**：主菜单「数据」展示六张族卡与完整证据说明。卡片展示用途、类型、覆盖、最新时点、最近拉取与所属任务；覆盖口径那句已经说过的提示（如 SEC 的「无申报 ≠ 缺数据」）不再另列一遍。点击族名进入详情。未知子路径（如 `#data/signals`）回到总览。
- **族详情 `#data/<id>`**：上半保留同一张族卡及拉取按钮，展示后端返回的最近 20 次拉取记录（触发来源、状态、成功/目标、范围、任务路径与目标来源、开始与完成时间、失败原因与 skipped 原因）。逐目标覆盖表列出最新时点、是否滞后与最近失败原因（单只拉取的失败只归那只；整族失败只归该次调用的目标清单，不外溢到另一路任务的目标）；「仅看滞后」只在前端过滤，不改变目标清单或浏览选择。时间戳统一显示北京时间，纯日期保持原样。
- **原始数据浏览**：日线、SEC、资讯、ATR 可从目标清单选择或手输标的，按回车或「浏览」确认，自动去首尾空格并转大写；未知标的显示「无数据，可点拉取」，读取不会隐式采集。日线只显示表格（OHLC、`adj_close`、成交量、拆股因子、修订次数、采集时间），表头区域保留拆股 / 分红调整说明；资讯显示标题链接、来源、发布者与发布时间；SEC 分开显示 `filings` 和未入库的 `sec_8k`。ATR 保留逐日旧到新顺序，显示 close、atr、atr_pct，注明「现算，与 `plan-1` 同一函数；不入库」及 `gate_hold` 证据说明。FRED 可切换 `BAMLH0A0HYM2` / `T10Y2Y`，显示实际值与修订次数；范围族直接展示 name / as_of / source / note 与成员最新日线，文件不可读时显示降级原因。
- **单只拉取**：仅日线、SEC、资讯显示「只拉这一只」；ATR 使用卡片「拉取日线」，FRED 与范围不提供单只按钮。完成后刷新卡片、最近记录、覆盖和当前浏览，保留所选标的。409 / 后台 busy、runner error、partial / failed 与采集记录写入失败会就地提示；轮询最多 15 分钟，超时只显示中性说明「仍在后台运行，稍后刷新」，不把仍在运行算作失败。

## 就地展开概念

v2.6 起，页面上的概念名旁有一个「ⓘ」（v2.10 起是 lucide `info` 图标）：点一下在原位置下方展开概念卡的标题、摘要和引用（`concept:<id>@<版本>`），再点收起，`Esc` 收起，同一页面同时只展开一个；「看完整卡」跳到「知识」菜单里的整张卡。概念索引首次使用时请求一次并在页面会话内缓存；索引加载失败时框里写「概念卡暂不可用：<原因>」，文字照常显示；索引里没有的 id 只显示文字、不显示按钮。「数据」菜单挂载的位置：

- 总览与族详情：六张族卡的族名（对应各数据族的概念卡）；页面顶部证据说明（证据状态）。
- 原始值浏览：日线的拆股 / 分红调整说明；ATR 的 `atr`、`atr_pct` 两列；FRED 当前所选序列（`BAMLH0A0HYM2` / `T10Y2Y`）；范围族的元数据。

挂哪个概念集中写在前端 `web/src/lib/knowledge.ts` 的 `ATTACH` 里，页面不写字面 id。

## API

| 接口 | 作用 |
| --- | --- |
| `GET /api/data/families` | 总览：六张卡 |
| `GET /api/data/families/{id}` | 族详情：卡片 + 最近 20 次拉取记录 + 逐目标覆盖表（每个目标的最新时点、是否滞后、最近失败原因） |
| `POST /api/data/families/{id}/fetch` | 手动拉取，body 可选 `{"ticker": "MU"}`；返回 `{run_id, status: "pending"}`。`run_id` 是 `core/runner` 的进程内运行号：页面轮询完成即就地显示结果；直接查询用 `GET /api/runs/{run_id}`（同一进程内有效），单族拉取不写 `job_reports`，「工具 → 运行记录」看不到它 |
| `GET /api/data/browse/{id}?ticker=&series=` | 原始值浏览：按标的的族传 `ticker`，FRED 传 `series`，范围族不传 |

错误体沿用 `{"error": "..."}`：数据库未配置 / 不可达 / 工作台旧表缺失 / 迁移 024 未执行 → 503（POST 在提交后台任务前就检查这些）；未知族 → 404；非法参数（派生族、族不支持单只、非字符串或非法 `ticker`、缺 `ticker` / `series`、未知序列）→ 400；该族正在采集 → 409。迁移 024 未执行时读取照常（无「最近拉取」一栏），拉取接口返回 503「需执行迁移 024」。

## 怎么验证一次

1. 打开「数据」：确认六张卡与完整 Phase 0 Gate HOLD 证据说明，点击「日线」进入详情。
2. 查看最近拉取表与逐目标覆盖表；勾选「仅看滞后」，确认只隐藏正常覆盖行，浏览候选不变。
3. 浏览输入 ` mu ` 后回车，确认规范化为 `MU`，日线为表格且有拆股 / 分红说明。点「只拉这一只」，完成后确认卡片、拉取记录、覆盖和当前原始值更新；失败原因或记录写入错误不应被刷新抹掉。
4. 依次查看六族：SEC 两段列表、资讯标题链接与北京时间、ATR 逐日值与现算 / `gate_hold` 说明、FRED 两序列实际值与修订次数、范围元数据与成员最新日线。FRED / 范围没有标的输入框与单只拉取按钮。
5. 浏览从未采过的标的，应显示「无数据，可点拉取」且不自动拉取；访问 `#data/signals`，应看到总览六张卡。接口不可达应显示后端错误，而不是伪装成无数据。

也可用接口核对一次：

```bash
curl -s "localhost:8787/api/data/families"          # 六张卡，session 为最近已收盘交易日
curl -s -X POST "localhost:8787/api/data/families/bars/fetch" -H 'content-type: application/json' -d '{"ticker": "MU"}'
curl -s "localhost:8787/api/data/families/bars"     # 最近一条 trigger=manual_family、scope=ticker
curl -s "localhost:8787/api/data/browse/bars?ticker=MU"
```

- 拉取后 `trade_plans`、`signal_candidates`、`pool_tiers` 行数不变，Discord 无消息。
- 同族正在采集时再发拉取 → 409「该族正在采集」。
- 浏览一只从没采过数据的 ticker → 200、`has_data: false`，不是报错，也不触发采集。

## 已知限制

- 旧调用 `Tally.ok` / `Tally.fail`（没有取数记录可传）写进 `detail.targets` 的项：`ok` 的 `attempts`、`http_status`、`elapsed_ms` 都是 `null`；`fail` 的 `attempts`、`http_status`、`elapsed_ms` 是 `null`、`error_kind` 是 `other`（传入的是 `FetchError` 时保留它自己的类别与次数）。只有改用 `Tally.record` 的采集路径才有尝试次数与耗时；`record` 的成功项 `http_status` 记 200（一次成功的取数按定义是 2xx），失败项的 `elapsed_ms` 是含重试等待的墙钟总耗时。
- 卡片「最近拉取」与 spec §8 的口径不同（2026-10-27 验收后用户决定）：spec 取该族最新一行，现在主行只取全族记录，单只拉取只在比它更新时另列一行，「最近成功」也只算全族记录。原因：验收时单拉一只不存在的代码，日线卡就整张显示 `failed`，而覆盖是 106/106。接口上 `last_run` 为最新全族记录，新增 `last_ticker_run`；详情页的最近 20 次记录不变，单只记录照常列出。
- 与 spec 的两处实现偏差（如实记录）：
  1. 批处理写的拉取记录 `run_id` 为空：记录在采集阶段末尾写入，此时拿不到任务的 `run_id`，靠 `job` + `started_at` 对照「工具 → 运行记录」里的运行报告（`started_at` 为采集阶段的真实墙钟，是个对照线索而非精确外键；`filings` 任务在任务返回后写记录，能带上 `run_id`）。
  2. 手动拉取的记录带的是 `core/runner` 的 `run_id`（经 `runner.submit(..., with_run_id=True)` 传入），不是 `job_reports.run_id`；它不写 `job_reports`，结果经页内轮询展示，或用 `GET /api/runs/{run_id}` 查询（同一进程内）。
- 已披露的目标清单兼容偏差：手动全族与批处理四族目标的并集口径是「当前观察池 ∪ 未平仓持仓」，而旧批处理循环为已清仓的历史持仓继续采集数据（plan stop 与财报提醒需要）；因此批处理的 `target_count` 可能比手动目标多出已清仓标的。这是保持旧主线行为的已知偏差，不是遗漏。
- SEC 申报有两条采集路径：`filings` 任务（监控清单或 `config.watchlist`）与 `workbench` 批处理（当前池 + 持仓），目标不同；卡片的「所属任务」指注册任务（`filings`），最近拉取行按记录如实显示实际 `job` 与 `targets_source`。
- 覆盖口径与滞后阈值是展示定义，不是规则参数，不用历史数据去调；FRED 的「覆盖 = 有观测值的序列数」与「滞后 = 最新观测早于 7 天」是两个独立口径，一条序列可以既覆盖又滞后。
- 浏览窗口固定（日线 120、ATR 60、资讯 60 天、SEC 180 天、FRED 120），不做日期筛选；窗口内完整读取、无第二层静默条数上限；画图是「标的」页的事。无接收时间的 `sec_8k` 标注「申报日未知」，不参与「最近一条申报日」统计。
