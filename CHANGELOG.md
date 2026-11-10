# Changelog

版本号与 roadmap 版本对应，见 [roadmap](docs/roadmap.md)。

## [3.2.2] - 未发布

今日与账户页卡片化、工具面板（[设计 spec](docs/specs/2026-11-10-v3.2.2-page-cards-and-tool-list-design.md)、[实施 plan](docs/plans/2026-11-10-v3.2.2-page-cards-and-tool-list.md)）。代码与测试已落地，尚未上线；无数据库迁移。

- 今日页将简报小节拆为独立卡片，按文字与数字两栏排布；右侧账户始终显示最新数字，行情显示简报日期的市场快照。
- 历史简报的今日操作显示当日文字且不提供写入按钮；接口缺少新字段时仍可显示整篇简报。
- 账户页拆为概览、持仓、成交、出入金、对账五张卡，操作入口位于所属卡片。
- 右侧抽屉新增只读工具面板，分五组列出 35 个可调用工具、说明与写入类型。
- 两个单份简报接口新增 `sections`、`lead`、`footer`、`market` 字段，不新增接口或迁移。
- 审查前的三套全量测试：离线 pytest 919 passed / 416 skipped；一次性库 pytest 1335 passed；前端 `npm test -- --maxWorkers=2` 497 passed / 71 files（默认并行首次运行有一项 AccountView 超时，限为两 worker 后通过）；构建成功，svelte-check 0 错误 / 0 警告。
- 审查后修复历史简报请求失败或等待时误显最新行情、草稿提示遮住导语的问题；定向验证 `TodayView.test.ts` 19 passed，svelte-check 0 错误 / 0 警告，未重复全量测试或构建。
- 画廊的隔离浏览器截图为空白，不能作为视觉通过证据；用户已明确「我来完成验收」，视觉与响应式布局验收由用户完成，结果待记录。

## [3.2.1] - 2026-11-09

工作台壳、纸面视觉与数据视图（[设计 spec](docs/specs/2026-11-09-v3.2.1-workbench-shell-and-data-views-design.md)、[实施 plan](docs/plans/2026-11-09-v3.2.1-workbench-shell-and-data-views.md)）。2026-11-09 经用户授权上线：备份后执行迁移 036、重建 `web/dist` 并重载页面服务，真实 Pi 冒烟通过（MarketCard 带 `call:1`、刷新回放、七视图与三侧览）。

- 三栏工作台：左栏七个完整视图与分组对话，中心默认最近对话，右侧仪表 / 行情 / 定时任务侧览；折叠与面板偏好持久化，窄屏用互斥 Sheet。支持对象深链、历史简报日期与 `⌘/Ctrl+K` 返回对话。
- 纸面视觉：浅 / 深灰阶与墨黑主色，标题衬线，8 / 12 / 14px 三档圆角；卡片无阴影，对话正文 13px / 1.55、限宽 760px。
- 35 个工具均有数据快照卡与真实来源行，通用卡覆盖其余形状；直播工具占位按调用分别解析，历史卡进入视口才读取。上下文芯片随用户原文存储，确认写入后共享缓存刷新并追加本页操作说明；切视图 / 切对话保留进行中的流。
- 四个读接口：`/api/market`、`/api/runs`、`/api/ledger`、`/api/chats/{id}/calls`；today 增 counts，对话增加最后活动时间、首条消息标题、消息 agent_session 与 SSE start 帧。
- 迁移 036 为 `v3_tool_calls` 追加 `result` / `truncated`，不回填历史；超过缺省 524288 字节只存截断标记，完整成功结果的哈希与 agent 信封保持原行为。Pi 从返回文本提取真实 call:N，事件工具名移除 `mcp__ia__` 前缀；`get_market` 追加 calc_version。
- 示例配置含 `daily_weekdays=[2, 3, 4, 5, 6]`、`daily_hour=11`、`daily_minute=30`、`tool_result_max_bytes=524288`；日程配置与 LaunchAgent plist 必须同步修改。
- 上线前三套全量：离线 910 passed / 412 skipped、一次性库 1322 passed、前端 472 passed，svelte-check 0 错误。[上线四步与执行记录](docs/operations/local-service.md#v321-上线四步2026-11-09-已执行)。已知外观问题：288px 侧览里 ETF 表数字折行、窄屏左栏 Sheet 右侧留白。

## [3.2.0] - 未发布

检验与复盘、研究与跟踪、页面的时间控件与金额（[设计 spec](docs/specs/2026-11-08-v3.2-retrospect-research-and-page-polish-design.md)、[实施 plan](docs/plans/2026-11-08-v3.2-retrospect-research-and-page-polish.md)，在 v3.1 的计划线、账户、简报与页面之上加三样东西；每日任务第 2 步、关注列表与旧口径（`screen-1`、`rule_levels`、`size`、`pending_cap=3`、`holding_check`）均未动）。按 spec §2 的目标七条：

1. **执行复盘**：每份已结束的计划一行——计划说的（行上最终价位）、实际做的（成交）、R 倍数、偏差标签（八个代码，价格从未进入场区间而没买记「照做」）；补录只算结果不打标签。全部从记录现算，不另存副本。
2. **规则检验**：按触发规则、形态档位、行业组三个键分组的样本数、胜率、平均 R、R 合计；样本数低于 `v3.retro_min_sample`（缺省 10）的组只写「样本不足」，不给数。
3. **如果照做会怎样**：没买的计划用后续日线模拟假设结果（成交、持有、未成交、进行中），单独一列、单独一张表，任何汇总不与真实成交相加。
4. **研究是一条独立记录**：用户在对话里点名股票写论点，agent 当场给结论三选一（可以出计划 / 值得盯 / 否决）；「值得盯」落一个宿主能用日线检查的条件（四种，agent 提出、用户确认）。
5. **研究每天由宿主跟踪，不调模型**：逐日补查条件（缺日线不判不推进）；条件到了才进当天待办让 agent 复看；一直不到就过期。观察中研究的股票进每日采集目标，不写关注列表。
6. **研究转成的计划不占 3 个待入场名额**（用户决定）：名额与替换受害者只数规则计划；其余纪律（一只一份有效计划、止损、账户、股数）全部照旧；入场类型加第四种 `pullback`；agent 自报入场下限对研究计划放宽到收盘 − `v3.research_entry_atr` × ATR（缺省 4）。
7. **页面的时间控件与金额**：记成交的日期与时间用原生控件（时区固定美东，偏移按日期与时刻取，夏令时切换日不取错）；出入金与对账的日期用控件；页面金额与价格两位小数带 `$`，后端 `price_value` / `price_text` 取到分。

- **迁移 035**（可重跑，只新建对象不改旧行）：`v3_research`（一只股票同时最多一份观察中的研究，部分唯一索引）、`v3_research_events`（只追加变化记录，宿主事件按（研究、交易日、类型）唯一）、`v3_plans.research_id`、`v3_briefs.weekly_md` 与 `pushed_weekly_at`。已在一次性库验证，2026-11-09 经用户授权在真实库执行（备份与核对记录见 [docs/operations/migrations-and-backup.md](docs/operations/migrations-and-backup.md)）；页面服务同日重载。v3.2.0 在 spec §13 第 3、4 步（定时交易日的研究复查与周报、一份真实研究走一轮）通过后发布。
- **工具面 35 个**（23 个两模式 + 12 个仅对话）：新增 `start_research`（chat）、`conclude_research`（两模式；`watch` 带条件是用户写入，brief 模式带新条件被拒）、`drop_research`（chat 与页面）、`get_research`（两模式）；`save_plan`（链接 ready 研究、跳名额、放宽下限、`pullback`）、`revise_plan`（研究计划同一下限、待入场修订止损同步 `initial_stop`）、`get_agenda`（research 待办、`free_slots` 只数规则计划、二次确认与替换跳过观察中研究股票）、`get_candidates`（`has_research`）、`compute_plan_levels`（`entry_types` 四种）的变化与简报模式范围的扩定见 [docs/operations/v3-facts.md](docs/operations/v3-facts.md)。
- **简报与推送**：新增第 5a 段「研究」（排在待入场计划之后，没有研究时整段不出现），第 2 段加研究变化、第 8 段加研究未决行、页脚加「金额单位美元」；Discord 摘要加研究条件到了的一行。每周最后一个交易日的运行在最终简报之后算本周汇总写进 `weekly_md` 并单独推一条（同周重跑不重推；没选定模型照常产出推送；错误日不产出也不补）。
- **页面**：4 个新接口（研究两组与单份、放弃研究、复盘视图带 `week` 切换）与两个新块（研究卡片组、复盘面板）；新组件 `ResearchCards` / `ResearchCard` / `RetrospectPanel`，`MetricValue` 加 `currency`，`ChatPanel` 支持预填。
- **与 v3.1 设计的出入**（spec §15，冲突时以 v3.2 设计为准）：待入场名额只约束规则名单的计划；`ENTRY_TYPES` 四种（加 `pullback`）；`check_agent_levels` 的 `entry_too_low` 对研究计划从 2 ATR 放宽到 `research_entry_atr`（缺省 4）；待办五类（加「研究复看」）；简报模式的范围加当天待办里的研究股票；简报十一段（加「研究」）；页面六块（加「研究」「复盘」）；`price_value` 从去尾零改为固定两位（取到分）；记成交时间从手填 ISO 改为美东日期与时间控件。
- 验收对照（B1–B19 与 Review Focus 六条各由哪个测试证明）见 [docs/operations/daily-run.md](docs/operations/daily-run.md) 末尾。

## [3.1.0] - 未发布

每日简报、连贯的交易计划、止损纪律、账户、页面与对话（[设计 spec](docs/specs/2026-11-06-v3.1-plan-line-account-and-web-design.md)、[实施 plan](docs/plans/2026-11-07-v3.1-plan-line-account-and-web.md)，把 v3 设计的第 2、3 步合成一个版本交付，并新增计划线、止损纪律、账户三样 v3 设计里没有的东西）。按 spec §2 的目标七条：

1. **每日简报定时产出并推送**：`python -m investment_assistant.daily run` 一条命令做五件事（采集、事实计算、计划与持仓复查、发草稿、简报会话 + 最终简报 + Discord），退出码 0/1/2/3；LaunchAgent 模板在 `scripts/launchd/`（安装需授权，运维说明见 [docs/operations/daily-run.md](docs/operations/daily-run.md)）。
2. **计划是一条跨天的线**：`v3_plans` 改造为状态七态的计划线（待入场 / 已买入 + 五个终态），一只股票同时最多一份有效计划（数据库约束保证），每次变化记一行带原因的记录（`v3_plan_events`）；待入场最多 3 份，名额满时每天至多替换一份。
3. **止损纪律**：买入即挂止损单（确认状态在计划上），止损只上不下，目标触及后次日起移动止损（`max(当前止损, 最高收盘 − 3×ATR, 成本)`，补录成本高于现价的例外），触及而没有卖出记录时每天排第一行提醒；到期与论点失效给「下次开盘卖出」。
4. **账户**：期初现金 + 出入金 + 成交算出现金、总资产、浮动与已实现盈亏；对账返回差额；作废成交 / 出入金后全部重算（FIFO 保护拒绝超卖作废）。
5. **信号变少**：待入场上限 3 份（`v3.pending_cap`），名额满时每天至多替换一只名次更高的候选；名单带连续天数与掉出原因。
6. **页面与对话**：外壳上一个页面四块（今日操作、今日简报、计划与持仓、对话）加账户条与设置面板；`/api/...` 接口与 SSE 流式对话；运行时、提供方、模型存库里（无内置默认，保存前检查可用性），同一时刻只允许一个运行时进程。
7. **适配周级波段**：计划有效期 5 个交易日（`v3.valid_sessions`，延续重起算），最长持有缺省 20 个交易日（`v3.max_hold_default`，从起算日算，到期为卖出指令）。

- **迁移 034**：`v3_plans` 计划线改造与八张新表（变化记录、持仓复查、出入金、作废、简报、对话、消息、设置），可重跑；价位约束放宽为「全空或全有 + 入场低 ≤ 入场高 < 目标」，不再要求止损低于入场（实施期间 ruling R5：补录计划的止损以补录时最新收盘为基准）。2026-11-07 已在真实库执行（记录见 [docs/operations/migrations-and-backup.md](docs/operations/migrations-and-backup.md)）。
- **工具面 31 个**（21 个两种模式 + 10 个仅对话）：新增账户 / 待办 / 延续 / 修订 / 持仓复查 / 一句话总结 / 出入金 / 确认止损单 / 调高止损 / 取消计划 / 作废 / 关注列表增删；`record_equity` 语义改为对账，`record_trade` 按计划线推进（清单见 [docs/operations/v3-facts.md](docs/operations/v3-facts.md)）。
- **新命令**：`daily run`（五件事，支持 `--no-collect` 与单次临时指定运行时）与 `daily settings show|options|set`。
- 验收对照（A1–A22 与沿用条款各由哪个测试证明）见 [docs/operations/daily-run.md](docs/operations/daily-run.md) 末尾。

### 上线当天的修正（2026-11-07）

- 每日任务 LaunchAgent：运行目录与页面服务一致（共用 `pi.lock`），补 `WorkingDirectory`。
- 采集：yfinance 的响应缓存钉住 curl 句柄，launchd 下（256 个文件描述符）日线采集耗尽描述符；每次取完日线 / 资讯清空缓存。
- 页面对话的工具全部返回 `unavailable`：Pi 的环境白名单滤掉了数据库连接串；MCP 配置缺省带仓库 `.env`。
- 简报：价格去掉存储尾零、股数未算写「未提供账户资金，未算股数」、区制同词不重复、待入场计划保存当天算第 1 天；当天保存的计划不再列为「再次上榜」，`continue_plan` 拒绝同一交易日的计划（`saved_this_session`）。
- 页面金额取到分、股数与成本去掉存储尾零；记出入金的日期按本机当天 0 点提交（后端要求带时区）；所有写请求报错时显示后端的中文原因，不只是错误码。
- 页面：两栏各自堆叠（简报不再被挤到计划卡片下方）；今日操作没有内容时显示小卡片；「持仓」组加「补录持仓」；空组显示原因；对话移到右下角「助手」悬浮按钮打开的侧边面板。

## v3（进行中）

- 迁移 033（删 v2 的表）已于 2026-11-06 经用户授权在真实库执行：先 `pg_dump` 全库备份到 `.local/v3/backup-before-033.dump`，61 张表删除，19 张保留表行数不变，`facts show` 照常；`migrate` 不再被删表保护拒绝，之后的新迁移可以正常应用到真实库。记录见 [docs/operations/migrations-and-backup.md](docs/operations/migrations-and-backup.md)。

## v3.0.0 — 2026-11-06

v3 agent-first 重做开工（[设计 spec](docs/specs/2026-11-05-agent-first-rebuild-design.md)、[第一阶段 plan](docs/plans/2026-11-05-v3-facts-and-tools.md)，提交 `9ad88ad..c7104de`）。第 0、1 步已落地；用户在第 0、1、5 步完成后决定发布 v3.0.0，第 2、3、4 步继续在 v3 下迭代。

**第 0、1 步交付：**

- 文档归档：v2 的路线、功能与架构文档移入 `docs/archive/v2/`（只读），v3 roadmap 启用；概念卡按出入 #1 推迟归档。
- Pi 无人值守可行性：十项检查 10/10 通过（2026-11-05，Pi 1.5.0），方法与结论见 [docs/operations/pi-unattended.md](docs/operations/pi-unattended.md)。
- 迁移 032（七张 `v3_` 表）+ `facts/repo.py` + `V3Config`（`v3` 配置键）；032 已在真实库执行（备份 `.local/v3/backup-before-032.dump`）。
- 纯函数事实层：市场环境、形态与行业因子、规则排序、覆盖、持仓视图与草稿渲染（`facts/` 的 factors / ranking / coverage / holdings / market / draft）。
- 并发采集与事实任务 CLI：`data.registry.run_adapter` 新增 `workers` 参数（缺省 1，与旧行为等价）；`python -m investment_assistant.facts run|show`（退出码 0/1/2）。真实网络实测：八族采集加补采 2 分 20 秒–3 分 04 秒（目标 5 分钟内）；2026-11-06 真实库成功路径通过（交易日 2026-11-05，名单 20 只，含采集 3 分 20 秒，不采集重算三次一致）。
- MCP 工具面：`toolsurface/` stdio JSON-RPC，brief / chat 两模式 18 个工具，每次调用记 `v3_tool_calls`；真实 Pi 客户端在一次性库与真实库上冒烟通过。
- 验收复核修复（`c784360`）：超出仓位上限时股数记 0 而非负数；`record_trade` 重放同一 `request_id` 返回原行；同档触发规则取样本大的一条；`EdgarSource` 在工作池下线程安全（节流、CIK 映射、按线程记账）；一条依赖墙钟日期的测试改为固定时钟。
- 运维文档：[docs/operations/v3-facts.md](docs/operations/v3-facts.md)（命令、退出码、草稿位置、配置键、并行期共用事实表、迁移 032 与三次试跑的实测记录）。

**与 spec 的三处出入：**

1. 概念卡不在第 1 步归档，推迟到 spec 第 5 步：旧系统的知识页与测试仍在加载 129 张卡，归档了旧系统就跑不起来。
2. v3 每日采集不含 `sec_text` 族（申报原文下载）：本阶段没有任何工具读原文，采了也没人用；申报列表（`sec_filings`）照采。
3. 对话模式的记成交、记本金、补数据三个工具提前到本阶段实现：属工具面这一层，且验收 9（记录本金后能算出股数）需要记本金的工具。

**第 5 步提前：移除 v2（2026-11-06）：**

用户决定以 v3 为主要迭代方向、接受第 2 步完成前没有每日推送和页面，所以把 v3 设计的第 5 步（旧系统下线）提前到第 1 步之后做，顺序变为 0 → 1 → 5 → 2 → 3 → 4，现在已落地的是第 0、1、5 步（依据与范围见 [设计 spec](docs/specs/2026-11-06-v2-removal-design.md)、[实施 plan](docs/plans/2026-11-06-v2-removal.md)；提交 `5c8b98d` 前端、`3a8a51e` 后端、`45c02c2` 概念卡、`4db0d3c` 裁剪保留模块与迁移 033）。

- **删除的规模**：后端从 256 个模块、48,601 行降到 102 个模块、9,631 行；前端删除 12 个分区页面与全部业务组件，约 2.9 万行（`5c8b98d`：199 个文件、29,456 行删除）；测试文件从 234 个降到 101 个；概念卡 129 → 40，89 张与原理页归档到 `docs/archive/v2/knowledge/`（保留 `indicator.`、`rule.`、`plan.` 三组；第 1 步当时概念卡推迟归档的出入，现已了结）；`config/roadmap/` 与只为 v2 服务的一次性脚本删除，`docs/operations/scheduling-and-notifications.md` 移入 `docs/archive/v2/operations/`。Python 依赖去掉 `langgraph`、`langgraph-checkpoint-postgres`、`langchain-openai`，前端依赖去掉 `echarts`、`lightweight-charts`。
- **保留的东西**：v3 闭包（`facts/`、`toolsurface/` 及其依赖）、`data/` 整层与 `derivation/` 的指标层、服务外壳（认证、静态托管、占位首页，组件画廊只在开发模式（`npm run dev`）下的 `#_ui` 可用）、Discord 发送通道（`notify/discord.py`、`notify/message.py`）。`facts` 与 `toolsurface` 的行为不变。
- **迁移 033（删 v2 的表）**：已写好（57 张业务表加 LangGraph 检查点表）并在一次性库验证，**真实库尚未执行，待用户授权**；执行步骤（先 `pg_dump` 备份）见 [docs/operations/migrations-and-backup.md](docs/operations/migrations-and-backup.md)。在那之前真实库比代码多出这些旧表，保留的代码不读不写它们。`migrate` 带删表保护：033 待执行且要删的表里有任何一张存在并有行时，整个 `migrate` 拒绝执行、一条迁移都不应用，需设置 `INVESTMENT_ASSISTANT_ALLOW_DROP_V2_TABLES=1` 确认（空库与新建库不受影响）。
- **关注列表没有写入口**：`facts` 的股票池读 `watchlist_items` 里 `status = 'active'` 的行，但 v2 的写入实现随旧系统删除，目前没有任何命令或工具能增删关注列表，只能直接用 SQL（示例见 [docs/getting-started.md](docs/getting-started.md)）；旧实现在提交 `d082400` 的 `investment_assistant/db.py`（`upsert_watchlist_item`、`delete_watchlist_item`），第 2、3 步需要时可以取回。
- **旧服务**：已 `launchctl bootout`，未重新加载；plist 保留，下次登录时 launchd 会加载它并启动外壳，说明见 [docs/operations/local-service.md](docs/operations/local-service.md)。
- **新增的守护**：`tests/test_no_dangling_imports.py`（包、测试与脚本里没有指向不存在模块或名字的 `investment_assistant.*` 引用）；`tests/test_migration_033.py`（删除清单、保留清单、可重跑、保留的包代码里没有对被删表的引用）；`tests/conftest.py` 的库夹具应用到 033 为止，保留的数据库测试都在已删掉 v2 表的 schema 上跑。
- **与 v3 设计的出入**：
  1. 切换条件：原为第 2 步连续 3 个定时交易日产出简报后卸载旧服务，现为第 1 步完成后即下线。
  2. 顺序：原 0 → 1 → 2 → 3 → 4 → 5，现 0 → 1 → 5 → 2 → 3 → 4。
  3. 新旧并行期：不再有；第 2 步的定时直接用 11:30，不必错开到 12:30。
  4. 页面：原为并行期新页面加进现有服务、旧分区原样保留，现为第 3 步直接在外壳上加页面。
  5. 版本：原为切换完成时记 `v3.0.0`，现为用户在第 0、1、5 步完成后决定发布 v3.0.0，第 2、3、4 步继续在 v3 下迭代。
  6. 删旧表：原为切换时执行，现为迁移写好并验证，执行另行授权。
  7. 验收 4：设计文档写的「`#_ui` 画廊可用」指开发模式（`npm run dev`）；构建产物不含画廊，与 `design-system.md` 一致。
  8. 保留范围：v3 设计 §9 写保留 `derivation/` 整层，实际只保留指标层（`derivation/indicators/`）；`derivation/chain.py`（推导 v2 的计划行，对象表由迁移 033 删除）已删，连带 `execution/plan_levels.py` 里只服务它的计划构造函数、只服务它的常量与版本名。
  9. 概念卡正文：保留卡正文里指向已归档卡的链接改成了纯文字（加载器只接受同目录的卡链接），所以 `get_concept` 返回的这些卡的正文与 `content_sha256` 随之变化；除此之外 `toolsurface` 行为不变。

## v2.15.0 — 2026-11-03

**已在本机发布，使用验收未全部通过。** v2.12–v2.15 一次发布为 v2.15.0，不打 v2.12.0–v2.14.0 空 tag。范围依据 [冻结 spec](docs/specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md)；工程部分已完成：三套全量、全链路演练（审查修复前后各一次）和一轮最终整体审查的结果见「验证」，与 spec 的差异和限制见下。2026-11-03 经用户授权，对真实库完成备份、迁移 031、roadmap 导入、`web/dist` 重建、服务重启和 spec §16.2 中可自动核对的部分，并在发布提交 `0416b45` 上打 tag `v2.15.0`；推送与替换 `roadmap.md` 未执行。逐项记录见 [验收记录 §1–§4 与 §6](docs/archive/v2/roadmap/v2.12-v2.15-acceptance.md)。

### 搬家

- 五层包与 core / workflow / views 边界，删除旧 workbench Python 包，公共事务与版本下沉、依赖方向守护；活文档使用 1D 新路径，冻结历史不改写。
- 数值快照、独立推导、既有账户输入保持原口径；最终全量验证结果由收尾任务填写。

### v2.12：数据层注册表

- 九族适配器、真实主备来源 / attempts / scope、补查队列与 22 项标准指标；两套 MA 与 SPY / QQQ 基准保留。
- Task0 探测事实为 Stooq 404、Nasdaq 可用；Stooq 仅解析与补缺机制，正式未注册，不能记为已上线备源。SEC 正文共用有效 filings.output_dir 与跨 scope 的 .budgets。

### v2.13：公司研究员与指定标的研究

- 首版 agent-4 公司研究，计划 schema 仍 agent-3；失败不阻断，修订 / 续期 / 补查 carried；指定研究只出档案，daily 忙时 200 queued。
- 真实原引用、档案版本与缺记录说明，研究页及推理阅读待使用验收。

### v2.14：组合经理

- 逐账户硬组合检查、真实账户 PM 意见、agent-portfolio-2 证据与拒单、行业参数、账户三类 markers。
- 关闭 portfolio_manager 跳过整个节点、不执行硬检查、None 不作组合拦截、Today not_computed；开启且硬结果已存后 PM 失败才 degraded / 保硬结果。

### v2.15：系统内 roadmap 与优化提案

- 人工种子、带 revision 的路线记录、北京周六（纽约周五收盘后）提案、冻结 evidence_pack、批准后 planned 版本与后台 brief、pending 读回和 Markdown 导出。
- 四开关默认全开、下次 daily 冻结、续跑沿用与真实变更历史；十三节点 daily-3 / stages-6，概念卡 129 张、原理页 13 个阶段小节；新功能文档与未执行验收清单。

### 版本

运行版本升 `agent-4`，计划 schema 仍为 `agent-3`；新增或切换的口径为 `stages-6`、`daily-3`、`agent-portfolio-2`、`agent-pm-1`、`agent-optimize-1`、`agent-impl-brief-1`、`portfolio-check-1`、`indicator-1`。`stages-5`、`daily-2`、`agent-portfolio-1` 等旧口径保留给旧运行与旧快照，不改写。前端 `package.json` 与 `package-lock.json` 仅根包版本改为 `2.15.0`，不变更依赖。

### 验证

- **一次性库全链路演练**（2026-11-03，真实库、网络、模型、通知均未触碰），审查修复前后各做一次：
  - 在 55433 一次性库新建独立库，`python -m investment_assistant.migrate` 从零执行 001–031 共 31 个迁移；`optimize.roadmap import --dir config/roadmap` 首次导入目标 1 / 版本 17 / 决策 124 / 证据 11 全部 inserted，立即重跑全部 skipped；`export` 得 114 248 字节，`test_roadmap_export` 的种子等价断言对它跑过，且与纯模板对种子的渲染逐行一致（5083 行，0 行不同）。
  - **每日运行是合成替身，不是真实 `daily run --manual`**：真实 CLI 会触发生产行情、新闻、SEC、模型取数器，无法完全离线，所以改调同一入口 `tasks.daily.run(trigger_source='manual')`，传入与 `tests/test_daily_workflow.py` 相同的替身（合成行情 / 新闻 / 财报日 / 文件来源，Agent 设为 `provider=off`，假时钟、内存检查点，通知与 SEC 扫描换替身，HTTP 层一遇调用即报错）。数据库、迁移、`job_reports`、`stage_runs`、`data_fetch_runs`、`portfolio_checks`、`feature_flag_changes`、锁和 `workflow check` 走真实代码与真实（一次性）库；CLI 参数解析层未被演练覆盖，由真实使用验收（spec §16.2）补。
  - 结果（修复前后相同）：`stage_runs` 13 行、13 个有效节点按 `stages-6` 顺序；`collect` 九族各一行（另有 `sec_monitor` 自己的一行 `sec_text`）；`portfolio_checks` 3 行对应 3 个账户；`workflow check --session` 7 项全部 ok、退出码 0。把 `portfolio_manager` 关掉重跑一次：`portfolio` 为 `skipped / feature_disabled`、无新增 `portfolio_checks`、`summary.features` 记 `false`，`workflow check` 仍通过。整体状态 `degraded` 来自合成替身没有基本面 / 行业取数能力、`sec_text` 一行失败、无 FRED 密钥，不是被测代码缺陷。
  - 修复后重演另核对：两次运行的 `job_reports.summary.check` 都存在且 `ok` 为真（七项均 ok）；`data_requests` 无残留 `queued`。后者在演练里是空集合下通过（演练没有产生补查请求与研究员数据），不是有负载下的通过。
- **最终整体审查**：范围 `7d9464d..c90a6cc`（625 个文件，配对改名后约 4.9 万行），按目录切成五区并行各一轮 opus 审查（数据与底座 / 分析与视图 / 执行·复盘·优化 / 编排与接口 / 前端与文档）。结果 Critical 0；原报 Important 21 条，另将 1 条 Minor（跨版本续跑）升级为 Important，共 22 条，去重后 16 项全部修复；Minor 52 条，需要说明的写进下面两节，其余不影响数据与口径。
- **审查后的修复**（一轮，后端与前端 / 文档两个修复者并行，控制器看 diff 与相关测试确认）：
  - 补查请求不再永久停在排队：手动运行与手动复查结束时本次未处理的请求结算为 `deferred / manual_run`，每日 `review` 收尾把更早的孤儿请求结算为 `deferred / orphaned`，`workflow check` 的补查项按运行日窗口核对，不再被前一天遗留的行拖成永久不通过。
  - 研究队列不再冲破共享预算：每份研究的期限取 900 秒与剩余共享预算的较小者，剩余不足 120 秒不开始，采集每族开跑前检查；`waiting_data` 重跑计入次数、达上限以 `waiting_data_limit` 关闭；停在 `running` 超过两倍期限的请求回队并计次；每日队列里的研究采集沿用冻结开关。
  - 组合检查口径：贪心造成的容量不足改判 `deferred`（原因码不变），`validate_orders` 只硬拦与顺序、挂单无关的原因；行业两道上限改为组内按顺序逐条。由此「挂单链换版」「当天止损退出后腾位」不再被冻结的检查结果错拦；模拟账户的组合检查显示「不调组合经理，只有硬结果」，不再写成「Agent 不可用」。
  - 数据与工具：Nasdaq 备源答「未知」不再覆盖主源已知的财报日（原先会让 `earnings_in_validity` 作废失效）；研究员的 `get_sec_hits` 按来源过滤后再限条数，`get_filing_text` 跳过目录页、正文去标签。
  - 编排与运行详情：每日运行把自动核对结果写进 `job_reports.summary.check`；升级前产生的 `daily-2` 失败运行不能被 `daily-3` 图续跑（`graph_version_changed`，409）；运行详情暴露 `research_auto_closed` 和 `review` 节点的补查计数。
  - 前端与文档：研究页把关闭 / 错误原因码译成中文，`brief` 在 `pending` 时也能重新提交，组合检查块显示正确原因，运行详情显示「自动结束 N 份」；迁移 031 运维步骤补上 `--dir config/roadmap`、预期导入数 1 / 17 / 124 / 11 与重跑全 skipped、`web/dist` 重建，入口文档里的旧数字（节点数、概念卡数、迁移编号）按实际更新；功能文档同步上述口径。
- **三套全量**（审查修复之后；离线与一次性库两套均经「只允许回环地址 + 指定库」的连接守卫）：
  - 离线 pytest：3215 passed、1406 skipped、0 failed，190 秒，外部 socket 0 次。
  - 一次性库 pytest：4620 passed、1 failed，43 分 23 秒。失败的是修复引入的研究员读原文回归（`get_filing_text` 把单一短 section 当成目录行），已修；修后 `test_agent_research_tools.py` 与 `test_agent_hosts.py` 共 16 passed，**未整套重跑**。
  - 前端 vitest（`NODE_OPTIONS=--no-experimental-webstorage`，`--maxWorkers=4`）：1149 个用例，1148 passed、1 failed。失败的是 `Fundamentals.test.ts` 并行时 5 秒超时，单独跑 8/8 passed，计划前基线同样超时（见限制）。
  - `svelte-check`：997 个文件，0 errors、0 warnings。
  - 构建：输出到临时目录成功，`web/dist` 未改动。
  - 对照：修复之前（`c90a6cc`）的一轮全量为离线 3210 passed / 1392 skipped、一次性库 4602 passed、vitest 1142 passed / 1 failed（同一个 `Fundamentals` 超时）、`svelte-check` 0 errors / 0 warnings；当时一次性库首轮的 1 个失败是测试桩缺 `breadth_tickers`、前端 3 处期望表过期（原因码键集、设置页签、图标表），均为补全等式期望，未放宽任何断言。
- **Task 级审查**：Task 0–6 每个 Task 完成时各经一轮独立审查，修复由控制器看 diff 与相关测试确认；Task 5 / Task 6 完成时均无未决的 Critical / Important。概念卡检查 129 张通过，文档链接测试在全量内。
- **尚未发生**：真实库迁移 031、`roadmap import`、`web/dist` 重建、服务重启、spec §16.2 使用验收，见「限制与待验收」。

### 与 spec 的差异

**命令与接口**

- **没有 `optimize.roadmap check`（D45）**：spec §12.3 与 §16.2-8 字面写 `optimize.roadmap import / export / check`；D45（§24）已定 `optimize check` 并入 `workflow check`，实际只有 `python -m investment_assistant.workflow check`。`--session D` 为汇总形态（推导、持仓状态、账户、`portfolio_checks`、提案无引用、`data_requests`、研究员状态各一项），`--since` 仍是旧的计划表核对。
- **推导的数据库核对从 `derivation` 移到 `workflow`**：spec §12.3 写 `investment_assistant.derivation check / stages`；这两个命令实际要读执行、分析、编排的仓储，无法同时满足 D6 依赖方向，实际入口是 `python -m investment_assistant.workflow check / stages`，纯重算留在 `derivation`，跨层读取放 `views/derivation.py`。
- **`data.registry fetch` 对逐标的族必须带 `--ticker`**：spec §12.3 写 `fetch <family> [--ticker]`；全族目标集由 `workflow.targets` 决定，`data` 层不得向上依赖，所以逐标的族只能指定单只拉取，整族拉取走每日 `collect`。
- **通知发送改为由入口注入（D44）**：spec D44 指定发送在 `workflow/nodes/push.py` 等处调用，同时禁止 `workflow` 依赖 `notify / services / tasks`；实际由 `tasks` / API 入口把发送回调、SEC 投递端口和有效通知配置显式注入，节点只负责调用时机、幂等和记账，模板、返回值、去重与失败语义不变。
- **接口为页面补了几个向后兼容的字段**：开关 `GET` 带 `history`；roadmap 版本与决策详情带 `version / history / brief / brief_status` 与 `decision / history`；提案行带取自生成时冻结证据包的 `evidence_rows`；推理视图的 `steps` 多一段 `research`。spec 只要求这些页面能力，没有冻结字段，没有新增端点，已冻结字段不变。
- **适配器接口比 spec §5.1 草图多几个入参**：`Adapter.targets` 在 `(conn, scope)` 之外增加关键字入参 `facts / explicit / universe_loader`，由 `workflow` / `views` 宿主注入既有的池、持仓、链算法产出的冻结目标事实（原签名要求 `data` 层自己算依赖上层规则的目标，与 D6 冲突）；`Source.fetch` 的返回注解放宽为含 `Fetched[...]`，以如实保留 attempts。字段名、顺序、冻结性不变。

**组合检查与拒单**

- **拒绝码集合**：D31 写 `validate_orders` 新增 `sector_cap`、`mutually_exclusive`，与 §7.1 / §7.3 的 `reasons` 集合矛盾；按更明细且自洽的 §7.1 / §7.3，新增 `sector_cap`、`max_per_sector`，并把首因可能是 `single_cap`、`total_cap`、`held_ticker` 的情形也列入公开拒绝码（前后端同名中文，原样返回首因，不映射成别的原因）；共享的 `max_shares` 在风险容量不足一股时归 `single_cap`，文案写明「单笔风险或单票敞口」。
- **贪心装不下判 `deferred`，只有顺序无关的原因才硬拦（D30④、§7.3）**：`max_positions`、`total_cap`、由前序候选累计预留造成的 `single_cap`、有在挂买单的标的，一律 `deferred`（原因码不改名）；`validate_orders` 只对 `sector_cap`、`max_per_sector`、对**已持仓**标的的 `held_ticker`、零预留下 sizing 已是 0 的 `single_cap`（含现金连一股都买不起）硬拦。spec 本就只把「装不下」定义为可下单的 `deferred`；此前实现把依赖挂单与贪心假设的结果也硬拦，会让换版的旧单被撤后新版本仍下不了单。
- **行业两道上限按组内逐条判（D30③）**：「同组内超 `max_per_sector` 只数时标互斥组」有整组拦与组内至多 k 条两种读法；采用后者——按信号日、ticker 的贪心顺序累加，放得下给 `ok`，放不下给 `max_per_sector` / `sector_cap`，组仍列进 `conflicts`；0 股候选不计入组敞口与只数。`portfolio-check-1` 尚未发布，现在改的成本最低；若用户本意是整组拦，需回退这一条与对应两条测试期望。
- **模拟账户的组合检查状态（D31、§7.4）**：§7.4 把「没有 commentary」一律显示为「Agent 不可用，只有硬结果」；模拟账户按 D31 本来就不调组合经理，所以有硬结果时 `status = ready`、`reason = paper_no_pm`、无 commentary，显示「模拟账户不调组合经理，只有硬结果」；真实账户无 commentary 仍为 `degraded / agent_unavailable`。
- **`portfolio` 节点失败后不能从该节点续跑，也没有节点级重试（§15）**：§15 写「`portfolio` 失败 → 运行 `failed`，续跑从该节点」。实现里节点吞掉异常、链继续往下走（`accounts` 读到 `missing` 后不拦），所以检查点已越过 `portfolio`，该节点不可续跑，`node()` 包装器的 60 秒重试也不会触发；失败的组合检查只能整次重跑。这是 spec 自相矛盾处（`accounts` 已消费 `missing`，回头续跑 `portfolio` 没有意义），保留实现。

**研究、补查与开关**

- **运行之外的研究入口读当前开关，`daily` 内沿用冻结值（Q13）**：Q13 写开关下次 `daily` 生效、一次运行内沿用冻结值；研究补采在没有冻结状态时读运行时当前开关（缺行视为开），只有 API 后台等运行之外的入口如此，`daily` 内处理研究队列时传冻结的 `features`。
- **手动运行登记的补查请求不同步补采（§5.5、§6.3）**：spec 写 `request_data` 在 `agent`、`review` 节点尾部或研究入口同步处理；手动 `run_agent` / `rerun_agent` / API 复查产生的请求没有 `agent` 节点尾部可处理，结束时直接结算为 `deferred / manual_run`（模型被告知「已登记」，实际只记 `deferred`，不补采）。每日 `review` 收尾另把早于本次 cutoff、`run_id` 不是本运行的排队行结算为 `deferred / orphaned`；带 `research:` 前缀、可能仍在后台研究的行只在早于 cutoff 一天以上才结算。
- **`waiting_data` 重跑计入次数、停在 `running` 的请求回队（D28）**：D28 写三次失败 `closed / max_attempts`，没有覆盖这两种情形；`waiting_data` 的每次重跑计 1 次，达到 `MAX_ATTEMPTS` 以新增的 `waiting_data_limit` 关闭（否则每天有新版本的档案 30 天自动关闭永不触发）；`close_stale` 把 `updated_at` 早于两倍 `RESEARCH_DEADLINE_S` 的 `running` 请求回 `queued` 并计 1 次（后台线程随进程重启消失，错误版本兜不住）。每日队列给出的较小期限在模型阶段前耗尽时不计次数（预算截断不是失败）。
- **`research_requests` 的 `request_id` 重放不比对内容（§6.3）**：spec 写重复 `request_id` 回放；实现回放时不比较 ticker、问题、期限，内容不同也直接返回已存的行，而开关、提案接口是「内容不同报 409」。前端每次提交生成新 UUID，单用户下实际风险低，未改。
- **`collect` 节点状态不因新三族失败降级（D13）**：D13 要求降级可见；`fundamentals`、`sec_text`、`sector` 只供研究员 / Agent 读取，不是筛选与计划的必需输入，失败记在各自的 `data_fetch_runs` 行与数据菜单，和 `universe` 同规则，不把 `collect` 记为 `degraded`。
- **`sec_monitor` 的正文调用只覆盖本次扫描出现新 filing 的标的（D47）**：D47 写下载改调 `sec_text` 适配器；范围保持原行为（只处理有新 filing 的标的），其余目标由 `collect` 的 `sec_text` 行覆盖并跳过已下载者，避免每次扫描给全部观察名单多一次 SEC 列表请求。
- **SEC 每日额度按实际下载次数计，附件占额度（§15）**：每只每天至多 3 份按「原文 + 附件」的实际下载次数合计，先预约再下载；本地复用不占额度；预算存放在有效 `filings.output_dir` 下的 `.budgets` 文件，跨 scope 共享（清空目录会丢当日额度，运维文档已写）。

**编排与可观察性**

- **`daily show` 不打印开关当前值与最近翻动（§9）**：§9 写「开关当前值与最近翻动打印在 `daily show` 里」；实际冻结开关的核对位置是 `job_reports.summary.features`（`GET /api/jobs/reports`），设置页「功能开关」显示当前值与历史。另外 `daily show` 固定按 13 个节点列出，旧 `stages-5` 运行的 `portfolio` / `propose` 会显示「未开始」。
- **跨版本续跑被拒（§10）**：spec 未涉及；升级当天，前一天失败的 `daily-2` 运行仍在续跑窗口内，用 13 节点图续跑会让 `stage_runs` 混入 `stages-5` 与 `stages-6` 的行。现在检查点 `graph_version` 不是 `daily-3` 时拒绝（`graph_version_changed`，HTTP 409），只能整次重跑。
- **知识卡（§14）**：§14 的「提议者那张卡」对应现有的 `agent.action_levels`（现有 112 张里没有 `agent.proposer`）；升版或新增的 7 张是 `agent.action_levels`、`indicator.atr14`（注明原 ATR 族）、`system.stages`、`system.daily_workflow`、`account.agent_portfolio`、`indicator.moving_average`、`system.fetch_policy`；总数 129 张（§14 写 130 张，§24 已更正为 129）。
- **`discuss@agent-discuss-1` 加了 `get_roadmap` 却没有升版（D20、D37）**：D20 要求白名单变了就升版，而 D37 与 plan 的接口字面锁定了 `discuss@agent-discuss-1`，两处互相矛盾，选定后者。工具集合变了但版本名不变；讨论工具由发起讨论的 API / 任务入口按运行绑定，`review` 层不反向依赖 `optimize`。
- **反方采纳率是运行级代理指标（D35）**：D35 的「反方被采纳率」在当前图里没有同轮逐条采纳标记，按已有可观测数据统计「有反方意见且后续成功修订的运行比例」，证据块与说明都标明它不证明逐条采纳或因果关系。

### 限制与待验收

**发布与使用验收的实际状态（2026-11-03）**

- **已执行**：真实库备份（`pg_dump`，SHA-256 已记录）、迁移 031（单事务，62 张旧表行数不变）、`roadmap import --dir config/roadmap`（1 / 17 / 124 / 11，重跑全部跳过）、`web/dist` 重建、服务重启、tag `v2.15.0`（指向 `0416b45`）。
- **spec §16.2 已通过**：第 1 项（四开关、数据九族加指标卡、roadmap 导入与导出）、第 2 项（手动运行 13 个节点、九族、运行报告自动核对与 `workflow check` 7 项 ok、运行详情新分支可读，旧 `stages-5` 运行仍显示 11 个节点）。**部分通过**：第 3 项（价格只有 yfinance；META 财报日主源网络失败后真实尝试了一次 Nasdaq 备源，备源也失败，未遇到备源成功的切换）、第 7 项（关闭 `portfolio_manager` 后整节点 `skipped / feature_disabled`、`accounts` 照常、无新组合检查行，再打开；页面分界线待用户确认）、第 8 项（`--force-propose` 生成 3 条待审提案，非提案日运行 `skipped / not_proposal_day`；批准 / 驳回待用户选择）。**未通过或未执行**：第 4 项（当天无新候选，没有 `agent-4` 首版）、第 5 项（指定研究）、第 6 项（「今日」组合块与改 `sector_cap`）、第 9 项（断开 Agent 连接）。
- **真实账户未开账**：真实账户没有开户日，组合检查只覆盖两个模拟账户，组合经理没有可点评的账户；用户开账后才能验证真实账户的组合检查与经理点评。
- **未执行**：推送；用导出件替换 `docs/roadmap/roadmap.md` 并保存冻结旧件（验收记录 §6 步骤 4a）。发布当天两次手动运行都因网络 SSL 抖动、SPY 当日缺失与模型连接超时而 `degraded`，不是代码缺陷。
- 连续 10 个交易日定时观察（spec §16.3）、四版各自的使用验收（§16.4：数据菜单能解释任一族来源与降级、指定股票得到可读档案、「今日」顶部一段就知道先做哪条、拿系统 brief 开了一版工并回填证据）均未开始；工程完成不代替使用验收，Gate HOLD 不变。模拟账户尚未积累足够前向证据，组合检查与 `agent-portfolio-2` 的对照结论不能据此下。
- 备源：Stooq 日线端点 2026-11-01 探测为 HTTP 404（五只标的全部），正式 `bars.sources` 只注册 yfinance，补缺 / `adj_close` 比例 / 分界机制只有离线案例；NVDA 拆股调整因无数据无法验证。Nasdaq 财报日备源可用；发布当天真实环境里只出现过一次「主源网络失败 → 备源也失败」，备源成功写入的切换还没有真实证据。

**测试与演练的边界**

- **一次性库全量在修复后没有整套通过一次**：修复后整套为 4620 passed、1 failed，失败的回归修好后只重跑了 `test_agent_research_tools.py` 与 `test_agent_hosts.py`（16 passed）。发布前若要整套证据，需再跑一次。
- **`Fundamentals.test.ts` 并行时偶发 5 秒超时**：「displays the twelve latest actual quarters…」单独跑稳定通过（8/8，约 2–4.5 秒），宿主机负载高时并行会超过默认 5 秒；在计划之前的基线 `7d9464d` 上同样并行跑出现同样的超时（连同 `Instrument`），不是本计划引入，未改超时或断言。发布后本机满载时前端套件可能偶发红，需要时给该用例单独调超时。
- **演练用合成替身**：见「验证」；真实 `daily run --manual` 与 CLI 参数层、真实行情 / SEC / 模型取数、通知投递都没有被工程自查覆盖；演练里补查请求与研究员数据为空集合，相应核对项只是「空集合下通过」。
- 受保护启动器在一次性库套件末尾报 3 个被拒绝的连接（`test_run_detail.py` 里三个用例用空连接串建检查点连接，被守卫在连出前拦下，代码按「检查点不可用」处理，用例全部通过），是已有现象；一次性库里另有 18 个以前遗留的 `wb_test_*` 临时 schema，未清理。

**升级与组合检查**

- **升级当天的一次误导**：发布当天，前一天失败的 `daily-2` 运行详情页仍可能显示「续跑」按钮，点击得到 409（`graph_version_changed`，有中文文案），服务端已拒绝，不会污染数据；该运行只能整次重跑。
- **组合检查的两处余项**：`portfolio` 节点排在 `accounts` 之前，模拟账户当天的结算（成交、过期、止损退出）和宿主撤旧单发生在 `accounts` 里，所以组合检查冻结的是结算前的账本。修复后因此造成的错拦只剩两处：**当天止损 / 退出的标的不能在同一交易日再买**（已持仓的 `held_ticker` 仍硬拦）；**权益分母比实际晚一天**（当日变动要到下一次结算才进入分母，真实账户同样，因为真实权益也在 `accounts` 里写入）。未移动节点，也没有在 `portfolio` 里提前结算。
- 贪心装入顺序是信号日从早到晚，不是收益预测；SIC 两位是粗分类，未归类不阻断不代表风险为零；`sector_cap`、`max_per_sector` 的默认值（0.35 / 3）不从历史拟合。
- 每个账户每个 session 只尝试一次组合经理（运行 id 按账户与 session 固定），临时失败当天不再重试，失败如实记录，硬结果仍在。
- `PM_DEADLINE_S`（300 秒）与 `PROPOSE_DEADLINE_S`（600 秒）目前只是**启动闸门**：模型调用本身没有超时钩子，不受这两个时长约束；§10 预算总表里这两个数字并不限制模型调用的时长。

**研究、补查与提案**

- **首版因模型异常结束时，研究员段与工具调用不落库**（异常行只记原因，`agent-3` 时代就是这样），所以失败那次的 token 不计入当日上限，补查重跑也退回 `disabled`；不影响单元结论的正确性。
- 提案日按运行 cutoff 的北京日期是周六判定（唯一口径 `optimize.evidence.is_proposal_day`，与 §8.3 字面一致）；纽约周五休市而北京周六时 `propose` 也照跑。
- 同一个北京周六重复运行 `daily`（手动重跑、`propose` 中途失败后续跑、`--force-propose`），`propose` 会再起草一轮，最多再写 3 条，只按未决提案标题去重，超出 Q11「每次至多 3 条」的本意。
- 研究请求每日额度（5 份）先计数后插入，没有加锁，并发的两个 `POST /api/research` 可能越过上限；直接调用 API 传小写标的会以小写入库（前端会转大写）。
- 模型不可用的日子里 `close_stale` 不执行：它只在 `agent` 节点尾部、模型可用时才跑，所以 30 天无新版的档案要等模型恢复后才会自动关闭。
- `sec_text` 主文在当日 3 份额度用尽时静默跳过、目标仍记 ok（8-K 在同样情况下则报失败），数据菜单看不出是被额度截停；`.budgets` 目录每只每个 session 留下两个小文件，没有清理。
- 冻结开关的核对记录 `job_reports.summary.features` 随 `job_reports` 的 30 天清理一起消失，超过 30 天的运行无从核对开关。
- Agent 子图的跳过行与修订行仍记 `agent-3`，只有首版记 `agent-4`（默认版本映射未改）；D38 要求的是运行版本升 `agent-4`、计划 schema 保持 `agent-3`，两者在首版上已经区分。
- 研究列表后端固定取 50 条、没有分页；`brief` 状态靠手动「刷新」读回，不自动轮询。

**页面与文档**

- 前端错误提示表只收了 4 个新错误码，`version_id_exists`、`proposal_already_decided`、`brief_already_ok` 等会落到通用文案（`version_id_exists` 的提示会误导，应换版本号）；roadmap 决策页的版本筛选按字典序排序（`v2.10` 排在 `v2.2` 前）；`decisions.yaml` 有 85 条 `quote` 为空串，页面会渲染空引用块。
- 概念卡 `agent.reject_reasons` 已列出 optimize 与 brief 的拒绝码，但 `applies_to` 元数据未补 `agent-4`、`agent-pm-1`、`agent-portfolio-2`、`agent-optimize-1`、`agent-impl-brief-1`，版本号未升。
- 验收记录 §4 的清单缺 §16.2-9 里「Agent 未连接时 `propose` 为 `skipped`」这一条，已在 §6 的使用验收步骤里补上，§4 保持原样。
- 活文档里个别历史段落还写着旧模块路径（`architecture.md` 的 `agent/conditions.py`、`derivation.py` 与「112 张」，`design-system.md` 的 `run_overview.py`，`features/legacy/README.md` 里 `refresh_local_filings` 的说法）；README 仍写「当前版本 v2.10.0」，属于发布后回填项。

## 未发布

### 修正

- **Agent 单元被「`'str' object has no attribute '__cause__'`」记成 `model_error`**（2026-10-31 定时运行，ROP）：`data/fetch_policy._chain` 沿异常链走时把 `reason` 属性也当异常压栈，而 `ssl.SSLError.reason` 是字符串，下一步取 `__cause__` 就崩；这一步只在「POST 不重发」策略判断请求是否发出时经过，所以恰好打在 ChatGPT 令牌续期经代理出 TLS 错误的那一次，AttributeError 穿过 `_run_agent_unit` 被记成模型错误，真正的网络错误文本也丢了。现在链上只跟进异常对象；新增测试 `test_post_policy_survives_a_string_reason_in_the_chain`。
- **代理抖动被当成「Yahoo 无数据」，筛选 `benchmark_missing`**（同一次运行，QQQ 等 19 只 10/30 日线缺失）：yfinance 默认 `hide_exceptions=True`，`history()` 与时区查询遇到任何传输错误都只打一行「possibly delisted; no price data found」并返回空表，`bars.py` 只能记成不重试的 `empty`。现在建立共享 yfinance 会话时关掉 `hide_exceptions`：传输失败按 `network` / `timeout` 走 fetch-1 重试，日线那一路只把 `YFPricesMissingError` / `YFTzMissingError` 收成原来的 `empty`（失败文案不变）。顺带的口径变化：财报日的 `4xx` / `5xx` 与资讯搜索的解析失败从此是该目标的失败而不是空结果。概念卡 `system.fetch_policy` 升到 `version: 2`；[data-menu.md](docs/archive/v2/features/data-menu.md) 同步。新增测试 `test_yfinance_transport_failure_surfaces_as_a_network_error`（死代理端口）与 `test_yfinance_no_data_answer_is_still_an_empty_result`。
- **并行工具调用撞 `agent_tool_calls` 的 `(run_id, seq)` 唯一键，整个单元记成 `model_error`**（2026-10-29 MU 2026-10-28 attempt 2 的 `seq 10`、2026-10-31 14:30 手动重跑 LIN 2026-10-30 的 `seq 8`，都在反方阶段）：`Recorder.record` 用 `len(calls) + 1` 编号再追加，两步不是原子的；模型一轮里并行发出多个工具调用时LangGraph 的 `ToolNode` 在线程池里同时执行它们，两个调用算出同一个 `call:N`（检查点里能看到 MU 的 `get_indicators` / `get_news` 都是 `call:10`、LIN 的 `get_trigger` / `get_concept` 都是 `call:8`，各缺一个号），运行结束逐条 INSERT 时撞唯一键，事务回滚、token 白花。现在编号与追加在同一把锁内完成，`call:N` 引用形式不变。新增测试 `test_concurrent_records_get_distinct_monotonic_seqs`（屏障确定性复现）与 `test_parallel_tool_calls_in_one_round_get_distinct_refs`（走真实 `ToolNode`）；[agent-track.md](docs/archive/v2/features/agent-track.md) 工具层一段补上编号规则。
- 三处都是后端修正，**无迁移，`.env` 与 LaunchAgent plist 不变**；需要重启服务生效，10/30 缺失的日线要手动补拉一次并重跑筛选，MU 2026-10-28 与 LIN 2026-10-30 两个单元要手动「重新运行 Agent」。

## v2.11.0 — 2026-11-01

账户与模拟交易（[spec](docs/specs/2026-10-31-v2.11-accounts-and-paper-trading-design.md)）：真实账户改为从成交与出入金计算权益，券商快照用于对账；新增 Agent 自管与照计划执行两种模拟账户，分别记录订单、成交、每日权益和决策依据。2026-11-01 复核通过后合入 `main`，前端包版本 `2.11.0`；测试与审查结果见下。[真实库只读核查](docs/archive/v2/roadmap/v2.11-acceptance.md)已于 2026-10-31 完成；**备份与迁移 030、重建 `web/dist` 与服务重启、真实数据验收、tag 和推送均待逐项单独授权**。

### 交付

- **账户账本 `account-1`**（`accounts.py`）：初始现金 + 净出入金 − 买入成本 + 卖出净所得；FIFO 持仓、费用与拆股、每日现金 / 市值 / 权益 / 盈亏、自开户修正 Dietz 收益（以开户首个交易日收盘权益为起点）和最大回撤。真实账户开账可带入已有持仓，按实物转入登记、不再次扣现金；快照与账本的绝对差额和比例差额同时不超容差才算一致（默认 100 美元与 1%），差异只提示、不改账。
- **模拟账户与决策**（`paper_strategies.py`、`agent/portfolio_check.py`、`graph.run_portfolio`）：两种策略只买可买集合内的 Agent 入场期计划，都受账户现金、风险、单票、总仓位和持仓数量上限约束。照计划执行按最大允许股数逐项挂单、修订换单、链结束撤单；Agent 自管可选择计划与股数、撤在挂买单、提前卖全部或一半，并解释订单与未卖持仓。宿主逐条拒绝超限订单，不缩数；模型不能改计划价位或取消止损、T1 卖半与时间止损。
- **模拟成交 `paper-fill-1`**（`paper_fill.py`）：入场复用 `entry-1`，持仓逐日步进与 `plan-shadow-2` 共用 `PositionState` / `step_position`；成交当天检查退出，同日止损先于 T1，T1 按 `max(开盘, T1)` 卖半，移动止损下限为买入成交价，时间止损在次一交易日开盘退出。持仓冻结买入时的计划，链之后的修订不改持仓价位；每链最多成交一次，入场作废或退出消耗整链；订单过期或撤单只消耗该版本，宿主拒绝的订单不消耗版本。
- **每日工作流**（`paper_accounts.py`、`workflow.py`、`tasks/daily.py`）：`accounts` 在 `review` 后、`push` 前，顺序为所有模拟账户补结算 → 真实权益 → 模拟持仓状态 → 本 session 决策。模拟账户按 `settled_through` 逐交易日补权益与成交，缺跑日期不补造 Agent 决策；同账户同 session 决策唯一。账户 Agent 共用独立 600 秒窗口（`ACCOUNTS_AGENT_DEADLINE_S`），token 仍计入当日总量；模型不可用只跳过新决策，已有挂单和自动退出照常结算。采集范围包含模拟持仓和在挂买单标的。
- **API 与读视图**（`api/routes/accounts.py`、`account_views.py`）：`/api/accounts` 支持账户列表、创建模拟账户、真实开账、改参数、关闭、概览、权益、成交、订单、决策与对比；已有成交、持仓、资金、纪律、轨道接口按账户读取，默认固定真实账户。账户页本月至今独立取上月最后交易日至当前截止 session 的精确完整权益，扣净出入金；不足时仅可回退同两日的有效快照，否则为空，SPY 使用相同端点，完整月报 `monthly-5` 不变。账户另给 `opening_session`，按交易所日历取开账当日或之后首个交易日，保留原 `opened_on`。模拟成交由宿主记录，人工成交、券商快照与出入金只允许真实账户；改参数从下一交易日起生效。只读 CLI 为 `python -m investment_assistant.workbench.paper_accounts show`。
- **账户页面**（`routes/Account.svelte`、`lib/accounts.ts`、`lib/components/account/`）：侧栏加入「账户」，页签为概览、持仓、成交、Agent 决策 / 执行记录、对比；提供真实开账、创建与管理模拟账户、真实成交与资金录入、权益与 SPY / 其他账户曲线、决策证据和订单结果。曲线以 `opening_session` 的完整正权益归一到 100，缺该日完整权益时不另取更晚基准。旧 `#review/trades`、`#review/account` 跳到真实账户；复盘页纪律与轨道对比可选账户，周 / 月复盘及复盘卡仍只看真实账户。数字保留后端 Decimal 字符串，统计块标截止时间与口径，对比不排名、平均 R 样本少于 5 时为空。
- **月度收益 `monthly-5`**：优先读取本月与上月最后一个交易日的最新账本权益，扣除端点之间净出入金；未开账或缺任一精确端点时回退 `monthly-4` 快照公式，标 `source = ledger | snapshot`。已冻结的周 / 月复盘和复盘卡保留当时版本，不重算历史结果。
- **旧规则计划重跑兼容**（`repository.store_plan`）：比较时将缺省的 `equity_source` 按旧 `snapshot` 解释，仅构造临时副本，不修改冻结记录；旧计划若改为显式 `ledger` / `null`，或金额、股数、快照 ID 发生变化，仍返回 `conflict`。
- **迁移 030**（[迁移与备份](docs/operations/migrations-and-backup.md)）：新增 `accounts`、`account_param_changes`、`account_equity_days`、`paper_orders`、`paper_decisions`、`paper_chain_done` 六张表；`trades`、`account_snapshots`、`cash_flows`、`holding_status` 增加 `account_id`，旧行默认归入固定真实账户。成交加 `recorded_by` / `detail`，持仓状态唯一键加账户；放行 `stage_runs.accounts`、Agent 的 `portfolio` 模式与工具阶段。缺迁移时账户接口返回 503，`daily` 与续跑前检拒绝运行。
- **知识与设计标准**：新增账户组七张概念卡，总数 112；月收益卡与原理页同步账本、对账、模拟规则与对比，节点卡保留旧版本并解释当前十一个节点。设计标准补 `MetricValue.raw`、账户 / 模拟订单 / 模拟决策 / 对账四个状态域、账户表格窄屏行转卡；架构、功能索引和调度说明同步。

### 版本

新增或切换的口径为 `account-1`、`paper-fill-1`、`follow-plans-1`、`agent-portfolio-1`、`stages-5`、`daily-2`、`monthly-5`。前端 `package.json` 与 `package-lock.json` 仅根包版本改为 `2.11.0`，不变更依赖。旧规则、原有提议 / 反方 / 复查提示词及其版本继续保留，旧快照、旧卡与旧成交状态不改写；原有推送内容仍只使用真实账户。

### 验证

- **审查**：Task 0–5 的 spec 与代码质量审查全部通过，Astra 最终代码审查及旧计划兼容修复追加复审通过，独立纯函数复验 6 条通过。2026-11-01 复核：opus 全量代码审查（`7baa913` 之后的提交与整个工作区）无 Critical，3 个 Important（自开户收益率起点、被拒订单消耗版本、撤单 / 过期原因枚举）与所列 Minor 已修复，未修项写入下方「与 spec 的差异」「限制与待验收」；修复经 opus 复审一轮，复审指出的拆股基准下卖半取整与文档残留已改正。
- **迁移 030**：一次性库上 001–029 之后以 `psql --single-transaction -v ON_ERROR_STOP=1` 从零连跑两次均成功；已执行初版 030（`92eab02`）的库再跑现版两次均成功，两库 `pg_dump --schema-only` 逐字相同，初版两条可被 NULL 绕过的 CHECK 与漏 `ticker` 的挂单唯一索引被替换。
- **后端全量**（复核修复后）：离线 `2724 passed / 1129 skipped`；一次性库 `3853 passed`，无跳过、无失败。
- **前端**：95 个测试文件、1101 条测试全部通过（Node 26 需 `NODE_OPTIONS=--no-experimental-webstorage`；默认并行度下 `Fundamentals.test.ts` 一条偶发 5 秒超时，该文件本版未改，`--maxWorkers=4` 下稳定通过）。
- **构建**：`svelte-check` 为 0 errors / 0 warnings，Vite 生产构建成功，保留超过 500 kB 的分包体积提示；产物只写到临时目录，现有 `web/dist` 的 hash 未变。概念卡检查 112 张通过，文档链接测试通过。
- **页面检查**：合成数据下账户页五个页签，以及 1280px 桌面和 375px 移动布局检查通过；真实库与当前服务上的数据验收尚未执行。
- **真实库只读核查**：2026-10-31 18:13:16 +0800，`trades`、`account_snapshots`、`cash_flows`、`holding_status` 均为 0 行；029 结构已具备，030 六张新表与四张旧表的 `account_id` 列均不存在。核查使用只读事务并回滚，未执行迁移或重启；这不代表真实业务验收完成。

### 与 spec 的差异

- **在挂买单唯一键补齐标的**：使用 `(account_id, ticker, chain_signal_date)`，修正草案遗漏 `ticker` 的键；同账户同一信号日不同股票的挂单可以共存，同一条链仍至多一个在挂买单。
- **同账户同标的至多一个未平持仓**：持有该标的时阻止其他链的新买单，避免一只持仓同时对应多个冻结计划；平仓后才重新检查其他链的机会。
- **保留未知计划的拒单意图**：仅 `status = rejected` 的买单允许 `plan_id` / `chain_signal_date` 为空，模型给出的未知计划 ID 保存在 `paper_decisions.output`；可执行订单仍须有真实计划外键与链字段，不能借此绕过计划范围校验。
- **账户节点独立冻结截止时间**：普通节点继续使用原运行的 `cutoff_at`；`accounts` 每次节点尝试开始时单独读取一次当前墙钟，session 不变，允许读取本轮上游在节点开始前已提交的计划，之后的版本仍被排除。同账户同 session 已有决策先复用，保留原截止时间、证据与订单；尚未决策的账户才使用新尝试的截止时间，显式写入阶段与决策的 `detail.cutoff_at`、有证据时的 `account.cutoff_at`，由决策 API 回显。所有读取仍受各自截止时间过滤。
- **自开户收益率以开户首日权益为起点**：spec §4.2 的公式只用 `opening_cash` 作分母，没有考虑 D25 开账持仓不扣现金；现改为开户首个交易日权益行扣除累计净出入金作起点，该行缺失或估值不完整（`bars_missing` / `stale`）、起点非正时为空。无开账持仓且开户首日无成交、无出入金的账户结果与旧公式相同；收益区间自开户首个交易日收盘起算，开户当天的盘中盈亏不计入。
- **宿主拒绝的订单不消耗版本**：`tried_plan_ids`、`validate_orders` 与 `follow_plans_orders` 都排除 `rejected` 订单，被拒的版本次日可改正后重下；作废、成交仍消耗链，过期、撤单仍消耗版本（D24 不变）。
- **撤单原因落成枚举并有中文**：Agent 撤单的被撤订单写 `agent_cancel`（原文保留在撤单行的 `reason`），过期写 `validity_expired`；新增枚举值 `account_closed`（关闭账户时撤销在挂订单），不在 plan 原列表内。中文表 `CANCEL_REASON_TEXT` / `VOID_REASON_TEXT` / `CHAIN_DONE_TEXT` 前后端各一份并由 `test_accounts_frontend_sync.py` 守护。
- **关闭账户撤销在挂订单**：同一事务把全部 `pending` 订单撤成 `cancelled`，原因 `account_closed`。
- **卖半向下取整**：T1 卖半与 Agent 卖一半取成交日股数的一半向下取整（结算时已知后来的拆股也按成交日股数取整），结果为 0 时不产生成交（T1 仍推进状态）；奇数股时账本数量与 `plan-shadow-2` 按比例算的 R 不再逐笔一致。
- **`monthly-5` 回退口径**：月末权益行带 `bars_missing` / `stale`（估值不完整）时同样回退快照；回退路径的快照按 `recorded_at ≤ now` 截止，出入金按 `known_flows`（按 now 的作废状态），与旧 `monthly-4` 不截止的读法略有不同。
- **新错误码 `record_before_opened_on`**：开账后不能补录开户日之前的成交或出入金（400）；资金快照不受限。
- **模拟账户 `discipline-2` 口径**：只按冻结计划与本账户持仓状态判定，不读链头与 `plan_stop` 事件。
- **Agent 运行列表不含 `portfolio` 运行**：账户决策的运行按 id 可查，列表入口不显示。
- **结算时防御性 `pending → rejected`**：`chain_consumed`、`plan_not_buyable`（以及卖半得 0 股的 `cannot_halve`）超出 spec §7.5 的状态流转，计数里不统计；`exit_instruction` 退出原因在当前路径不会产生（时间止损记 `time_stop`）。

### 限制与待验收

- 模拟成交依赖日线，默认每笔费用 0，无滑点、不模拟部分成交；日线无法还原盘中路径或成交队列。模拟时间戳只保证 FIFO 顺序（必要时后一笔加 1 微秒），不能当作真实盘中时点或成交证据。
- 缺当日收盘记 `bars_missing`，沿用历史收盘时另标 `stale`；完全没有报价时内部该标的市值暂计 0 并标不完整，不能把含缺价的权益当成完整市场估值。
- 真实与模拟账户的初始现金都须大于 0，真实账户即使带入实物持仓也同样要求；现金、快照与出入金的录入完整性会影响账本与对账。
- 账户起点、金额和出入金可能不同，曲线以开账当日或之后首个交易日的完整正权益归一到 100；该日缺失或估值不完整时不绘制账户曲线，各账户的起点和收益区间仍可能不同。这些统计不证明策略优劣或规则 / Agent 有效。真实库迁移、服务重启和手动验收完成前，运行结果与当前真实数据状态保持待核实。
- **Agent 自管账户的模型调用在账户写事务内进行**：调用期间持有账户锁，工具读取共用该连接；长时间调用会占住写事务。
- **新建模拟账户默认次一交易日开户**：建账当天运行 `daily` 不产生决策，首笔挂单在开户日收盘后下、再下一个交易日成交。
- **开账同一 `request_id` 重放返回 409 `already_opened`**，不是幂等回放。

## v2.10.1 — 2026-10-31

修正 v2.10 真实验收发现的两处「今日」布局问题，只改前端样式。功能、接口、口径、地址不变；**无迁移，`.env` 与 LaunchAgent plist 不变**。版本号：`web/package.json` 与 `web/package-lock.json` 的根包 `2.10.1`。

### 修正

- **「今日」在 1024–1280px 下整页横向滚动**（v2.10 验收发现，CHANGELOG v2.10.0「已知限制」里预判过的那条）：计划表最小宽度约 1200px，`DataTable` 在 ≥ 1024px 为了粘性表头让容器 `overflow-visible`，表格经由几层 grid 祖先（`min-width: auto`）把卡片、页头和整页一起撑宽，页头右侧按钮被推出可视区。现在 `sticky` 的表先测量（`ResizeObserver` 加窗口 `resize`，测量时临时把容器设成可滚动），放得下才让容器 `visible`、表头吸顶，放不下就在容器内横向滚动；`sticky={false}` 的表（含「今日」计划表）与测量前一律在容器内滚动。真实服务上复测：1024 / 1280 / 1440 下 `main` 不再横向溢出，1280 下「运行记录」表放得下、表头照常吸顶。
- **375px 下「今日」计划卡溢出**（v2.10 验收发现）：状态行的长标签「Agent 未给出计划：模型调用失败，以下为规则基线」是不换行的 `Badge`，宽 305px，超出屏幕 55px。`DataTable` 卡片视图里的徽标改为可换行（最宽到单元格、圆角 10px，单行仍是胶囊形）；表格视图不变。复测 375px 下没有元素超出屏幕。
- 两处只改 `web/src/lib/ui/data-table/DataTable.svelte`（原 1 条测试改写为 5 条，前端 1003 条全过，`svelte-check` 0 错误）与 [design-system.md](docs/design-system.md) 对应规则；验收记录见 [v2.10 验收记录](docs/archive/v2/roadmap/v2.10-acceptance.md) §6。

## v2.10.0 — 2026-10-31

设计标准与界面重构（[spec](docs/specs/2026-10-30-v2.10-design-system-and-ui-refactor-design.md)、[实施 plan](docs/plans/2026-10-30-v2.10-design-system-and-ui-refactor.md)）：前端九个版本逐页堆出来，同一状态各处各一种颜色与写法；本版写下一份设计标准（[docs/design-system.md](docs/design-system.md)，活文档），并把全部路由和组件迁到统一的原语层。**纯前端重构：不加功能、不改接口、不改口径、不改地址**（`lib/router.ts` 与 `web/src/lib/*.ts` 除 `theme.ts` 外一字未动；与后端逐字对齐的文案常量由原有跨语言测试照旧守护）。状态：`engineering_done`、`user_acceptance_pending`——**无迁移、无 `.env` 变化；`web/dist` 未重建、服务未重启、未打 tag、未推送**，页面目检与真实数据核对留给 Task 9（逐项授权）。

### 交付

- **Tailwind v4 CSS-first**：`@tailwindcss/vite` 取代 `tailwind.config.ts` 与 PostCSS（两个配置文件删除）；令牌值在 `web/src/styles/tokens.css`（浅 / 深两套，一律十六进制，两种图表库的颜色解析不认 oklch），`styles/app.css` 用 `@theme inline` 映射成类名。令牌采用 shadcn 命名但不采用其默认取值：次要文字 `text-muted-foreground`、次级表面 `bg-muted`、强调 `primary`；旧 `text-muted` / `accent` 与 shadcn 同名异义，Task 1 一次性改名、不留别名（`test_no_legacy_token_names`）。首次打开跟随系统深浅色，状态色在两套里各调一次且满足 AA（`tokens.test.ts` 逐条断言）。字号仅 `text-xs` … `text-xl` 五档，圆角一个值。
- **通用层** `web/src/lib/ui/<组件>/`：shadcn-svelte 1.7.2 经 CLI 复制的 18 个组件（底层 Bits UI 2.20.2），加 `DataTable`（≥ 1024px 粘性表头、数字列右对齐、已结束行置灰、窄屏 `cards="narrow"` 行转卡、「显示更多」）、`ConfirmDialog`（后果一句话、动作名按钮、焦点在取消）、`Field`、`TabNav`（路由级页签 = 链接 + `aria-current`）、`notify`（`svelte-sonner` 的 toast，主题读本项目 `lib/theme.ts`，失败 toast 不自动消失）、`useNarrow()`（`max-width: 767px`）。
- **领域层** `web/src/lib/ui/domain/`：`status.ts` 一张状态词汇表（`TONES` 领域 → 键 → 语气、`LABELS` 中文，已有中文常量按引用复用）与 `StatusPill`（未知键开发模式抛错）；`DataBlock`（加载 / 无数据 / 没算 / 降级 / 失败五态）、`MetricValue`、`CutoffTime`、`SampleSize`、`CalcVersion`、`VersionChip`、`PlanLevels`、`RefLink`、`SourceBadge`（为 v2.11 预留）、`PageHeader`、`Section`、`Icon`（`@lucide/svelte`，显式映射表 `icons.ts`）。缺值写「没算」或状态文字，不再用「—」或空白表达状态。
- **页面迁移**：应用壳、今日、计划与讨论（Task 5）；标的与复盘、K 线（Task 6）；知识、数据、工具、设置（Task 7）全部只组合 `lib/ui/` 原语；emoji 全删，导航与按钮图标走 `Icon`；不可逆动作（续跑、重新运行 Agent、作废成交 / 流水、断开连接、归档、撤回补充）经 `ConfirmDialog`；操作成功改 toast；窄屏（< 768px）的「今日」计划表、计划页、运行详情行转卡；多栏卡片网格按 ≥ 1024 两栏或四栏、768–1023 两栏、< 768 单栏。
- **图表主题** `lib/charts/theme.ts`（`readChartTheme()` 从 CSS 变量解析十六进制）与 `EChart.svelte`；`CandleChart` 的涨跌、标记、价位线、作废计划全部取令牌，主题切换时重建；图表色 `--chart-rule` / `--chart-own` / `--chart-agent` 三种来源色与 `--chart-1…5` 序列色相互拉开。
- **组件画廊** `#_ui`：只在开发模式由 `app.svelte` 动态导入，不进 `lib/router.ts` 的路由联合，生产包整体剔除（Task 8 退出条件核对过，见下）。
- **静态守护** `tests/test_frontend_design_guards.py`（Task 4 起阻断）：原色类名、十六进制 / `rgb()` 字面量、emoji、`<style>` 块、裸 `<button>`、`{@html}` 恰好两处、原生 `confirm` / `alert` / `prompt`（`navigation-guard.ts` 永久豁免）、状态词汇覆盖后端枚举；存量靠分片基线容纳，各迁移波删自己那片，Task 8 删整个目录并加 `test_baseline_retired`。
- **删除**：`lib/components/Modal.svelte`、`Drawer.svelte`、`Skeleton.svelte`、旧 `StatusPill.svelte`（`tests/test_dashboard_frontend_source.py` 改查 `lib/ui/domain/StatusPill.svelte`、`lib/ui/sheet/`、`lib/ui/skeleton/`）、`tailwind.config.ts`、`postcss.config.js`、`tests/frontend_design_baseline/`。
- **文档**：[design-system.md](docs/design-system.md) 首版（Task 3）与收尾补齐；`docs/architecture.md` §5 加 v2.10 一段；`docs/README.md` 列入设计标准与 spec / plan 链接；roadmap 状态。
- **提交**（`git log --oneline 4ae2339..HEAD`，自下而上）：`62286fb` docs（plan 去重，另一会话的文档提交）、`3932e4f` Tailwind v4 与令牌类名改名、`f44b00b` 令牌 v1 与通用层、`960d1d1` Task 2 审查修复、`ab6c93a` 领域层与状态词汇、`281df14` 守护与分片基线、`eea7be8` Task 3 审查修复、`251d615` 壳 / 今日 / 计划 / 讨论、`68a6340` 第一波原语缺口、`04eee7e` Task 5 审查修复、`2991f96` 标的与复盘、`0ef74fe` 知识 / 数据 / 工具 / 设置、`813258a` Task 7 审查修复、`92da2c7` 第二波原语缺口、`d0448d0` Task 6 审查修复、`51216a1` 退役旧组件 / lucide / 基线、`da76090` 全量 code review 修复波。

### 依赖与版本

- 新增（精确版本，不带 `^`）：`tailwindcss` 4.3.3、`@tailwindcss/vite` 4.3.3（devDependencies）；`bits-ui` 2.20.2、`@internationalized/date` 3.12.4、`@lucide/svelte` 1.65.0、`svelte-sonner` 1.2.2、`tailwind-variants` 3.3.1（不用 4.0.0，超出 registry 范围）、`cn` 0.3.3（dependencies）。`@tailwindcss/upgrade` 与 `shadcn-svelte` CLI 只用 `npx` 一次性运行，不进 `package.json`。
- 删除：`tailwindcss@3`、`postcss`、`autoprefixer`、`lucide`。不装 `tw-animate-css`、`mode-watcher`、`clsx`、`tailwind-merge`。其余依赖不升级（Vite 6、Vitest 4、ECharts 5、lightweight-charts 4 等的新主版本不在本版范围）。
- 版本号：`web/package.json` 与 `web/package-lock.json` 的根包 `2.10.0`。口径、提示词、模型层版本一律不变；**无迁移，`.env` 与 LaunchAgent plist 不变**。

### 与 spec 的差异（plan 2026-10-30 仓库核对，D1–D18；spec 不改）

- **D1** spec §2.1 的规模（`web/src` 12 844 行、9 个路由文件、40 个组件、12 个 `lib/*.ts`）：v2.9 合入后实际 22 464 行（含测试）、`routes/` 8 个页面文件加 `#discuss/<id>` 由 `Discussion.svelte` 承担 = 9 个路由、`lib/components/` 44 个组件、18 个 `lib/*.ts` 非测试模块。
- **D2** spec §2.2 的计数：原色类共 122 处、30 个文件；`<table>` 30 处、18 个文件；`<button>` 133 个、其中带 `underline` 37 个；守护按全部 Tailwind 色名加 `white` / `black` 检查（比 spec 的 11 个色名宽）。
- **D3** `{@html}`「只允许一处」→ 现有两处（概念卡正文、原理分节正文），都在 `Knowledge.svelte`，守护按「恰好两处」（Q2）。
- **D4** 「不得出现 `window.confirm`」→ `lib/navigation-guard.ts` 的「研究编辑未保存，离开？」依赖同步返回值，列为永久豁免、行为不变（Q1）。
- **D5** 「期望文本一字不改」→ `Settings.test.ts` 的 `'✅ 成功'` 改为 `'成功'`（去 emoji，Q3），是唯一例外（实现期另有一处，见下）。
- **D6** `Knowledge` 的 `<style>` 不是直接删除，而是搬到 `styles/knowledge-body.css`（只用令牌变量），负责 `{@html}` 内容排版。
- **D7** `tailwind.config.ts` / `postcss.config.js` 在 Task 1 就删（codemod 已迁入 CSS）；令牌改名不留旧名别名（同名异义）。
- **D8** `sonner` 改读本项目 `theme.ts`，不装 `mode-watcher`、`tw-animate-css`。
- **D9** `@lucide/svelte` 锁 1.65.0（registry 声明 `^0.561.0`，1.0 无破坏性改动，`icons/<name>` 子路径不变）。
- **D10** 只采用 shadcn 令牌命名、不采用其 oklch 取值；不引入 `--secondary`、`--accent`、`--sidebar-*`。
- **D11** 画廊 `#_ui` 不进 `lib/router.ts` 的 `Zone` 联合（`tests/test_dashboard_frontend_source.py` 逐字钉住），在 `app.svelte` 按 `import.meta.env.DEV && hash === '#_ui'` 动态导入。
- **D12** 路由级页签（复盘、知识、工具、设置的子页）按「导航用 `<a href>`」做成 `TabNav`；Bits `Tabs` 只留给不改地址的页内页签（本版除画廊外没有页面使用，运行详情三块保持纵排 `Section`）。
- **D13** `PlanTable`、`NodeGraph` 的 900px 统一为 `useNarrow`（< 768px），相关测试的查询串随之改。
- **D14** `Button` 尺寸加 `icon`（28px 方形）。
- **D15** 守护从 Task 4 起就是阻断（分片基线只减不增），不是 spec 写的「波 0 只报告、波 4 转阻断」。
- **D16** 数据族「三色」在代码里不存在，族卡显示的是 `data_fetch_runs` 的 `success / partial / failed / skipped`，词汇表用 `fetch` 域；旧 `StatusPill` 的 `green / yellow / red` 键无调用方，不进新表。
- **D17** 时间标注「北京时间」→ `fmtBeijing` 输出后缀是「北京」，`lib/format.ts` 不改，`CutoffTime` 原样使用。
- **D18** 每波验收截图（`docs/roadmap/v2.10-acceptance/`）→ 开发服务器没有后端数据（真实服务有 HTTP Basic 认证，Claude 不代输密码），各波只截画廊，页面截图集中在 Task 9。

### 实现偏差与已知限制

下面是实现期相对 plan 的偏差，以及审查中接受而没有修的限制。

**流程与构建**

- 构建一律输出到 scratch 目录（`.superpowers/…/build-dist`），**`web/dist` 未重建**：真实服务每次请求从 `web/dist` 读盘，原地构建等于上线半成品。**Task 9 重启服务前必须先 `cd web && npm run build`**。
- 一次性库全量 pytest 在 Task 8 跑了一次做回归（plan / spec §8.1 原写「无 SQL 改动，不跑」）。
- `tests/test_no_zhipu_residue.py` 因冻结的 v2.10 plan 文档提到该测试自身的文件名而失败（`4ae2339` 引入，早于实现）；收紧为忽略对守护自身文件名的引用，白名单未扩。

**Task 1 样式引擎**

- codemod 把非类名的单词 `shadow` 误改成 `shadow-sm`（`growth.ts` 的 `FACT_KEYS`、Review / SideNav / ReviewCard 的 id 与键、测试名），已全部还原；多行 class 里的 `rounded` 漏改，手工改为 `rounded-sm`。
- v4 固有差异未补偿：`hover:` 只在可悬停设备生效；`space-y-*` 改为给非末项加下边距；占位符颜色改为 `--muted-foreground`；`package-lock` 里传递依赖 `postcss` 等小版本随锁文件重算上移（直接依赖无升级）。

**Task 2 通用层**

- shadcn-svelte CLI 需临时取消 `HTTP(S)_PROXY` 才能拉 registry；18 个组件全部经 CLI。`@lucide/svelte@1.65.0` 的 `loader-2` 只剩无类型别名，`sonner` 改用 `loader-circle`；registry 组件用的 `data-open` / `data-active` 变体 Bits UI 2.20.2 不输出，统一改写为 `data-[state=…]` / `data-[orientation=…]`。
- `svelte-sonner` 无条件调用 `matchMedia`，jsdom 下 `Toaster` 不渲染（测试局部桩，页面测试断言对 `notify` 的调用）；`vitest.config.ts` 加 `css.include` 以便 `?raw` 读 CSS；新增 `web/src/vite-env.d.ts`。
- 接口相对 plan 的增减：`Badge` 无 `variant`；`Alert` 变体 `info | warn | danger`，`info` 默认 `role="status"`；`SheetContent` 加 `width`；`Table.Root` 加 `containerClass`；`candleOptions` 返回 `{ chart, series }`；`DataTable` 加 `expandLabel`、`ariaLabel`；`TabNav` 项加 `badgeLabel`；`ConfirmDialog` 加 `children`（表单体，如作废理由）、`confirmDisabled`，打开时焦点落在「取消」，`busy` 时 `Esc` / 遮罩 / 取消都不响应。
- `DataTable` ≥ 1024px 为粘性表头用 `lg:overflow-visible`，宽表在 1024–1280px 可能撑出页面横向滚动；放在 `Card` 里的 `DataTable` 在修复波里另加 `lg:in-data-[slot=card]:overflow-x-auto`（`Card` 的 `overflow-hidden` 本来就让粘性表头失效），卡片里的宽表在任何宽度都在卡片内滚动、不被裁掉。
- `Collapsible` 在挂载即展开的特定用法下内容保持 `hidden`（侧栏工具组复现），侧栏用 `forceMount` + `{#if}` 绕过，规则写进 design-system.md。

**Task 3 领域层**

- `chain.invalidated` 语气为 neutral（`ENDED_STATES` 终态、行置灰），spec §4.4 danger 里的「作废」留给作废成交类异常。`LABELS` 复用现有常量，两处例外是副本加键：`execution`（`EXECUTION_TEXT` 无 `unplanned`）、`node`（加 `missing` 缺证据 / `not_recorded` 未记录，文案取自 `workbench/run_overview.py`）；另新增 `consistency` 域；`instruction.exit` 为 danger（保留旧版红色醒目）；`agent.pass` 文案为「不建议买入」（修复波对齐）。
- `RefLink` props 为 `{ref, onFact?, onCall?, expanded?}`，依赖 `lib/components/ConceptTerm`（分层倒置，本版接受）。`StatusPill` 文字用 `text-foreground`，语气由圆点、边框、淡底表达（着色文字在浅色主题达不到 AA）。`PlanLevels` 加 `lead` / `sharesLabel`，计划表逐字保留原「区间限价 95.10–97.30 · 止损 … · 120 股」写法。

**Task 4 守护**

- 正则收紧两处：`<button(?![\w-])`（不把 `<button-group>` 算原生按钮）、`\{@html\s+\S`（注释里的 `{@html}` 不计数）。审查加强：SQL 词汇取最后一次 `CREATE` / `ALTER … ADD CONSTRAINT` 的定义；原生弹层另抓带字符串参数的裸 `confirm('…')`、`window?.`、`globalThis.`；原色类另抓方向边框、`ring-offset`、`shadow`、`accent`、`caret`、`placeholder` 与 `fill/stroke/ring/from/via/to/outline/divide/decoration-(white|black)`；扫描非空断言；每个（检查, 文件）至多一个分片。分片计数大于实际值时不报错（plan「≤ 记录值」规则），只拦超额与过期条目。
- 守护仍能绕过的写法：`confirm(变量)`、CSS 命名色（`text-[red]`、`style="color:red"`）、`<svelte:element this="button">`。

**迁移波（Task 5–7）与修复波**

- 「今日」接口无截止时间、计划页接口无版本号与截止时间，故两处未用 `CutoffTime` / `VersionChip`（只加 `CalcVersion`）；`AgentPlanCard`、`KnowledgeRefs` 因测试钉住文本未换 `VersionChip` / `PlanLevels`。
- 期望文本变化（§8.1 例外，除 D5 的 Q3 外）：运行详情确认按钮 `'确认继续'` → `'从此节点继续'`（spec §4.7 要求确认按钮写动作名）。设置页测试夹具 `status: 'green'` → `'up'`（`green` 不是后端会产生的值，新 `StatusPill` 在开发模式遇未知键抛错）。另有两处纯选择器改动：`Today.test.ts`、`workbench-scenarios.test.ts` 的 `getByText(/站上 MA50 30.0%/)` 改为 `/站上 MA50/` 加 `toHaveTextContent`（`MetricValue` 是子 `<span>`）。
- 结构性变化导致的测试改写（期望片段不变）：版本链、「其他触发」、窄屏计划卡从整行文本变为表格单元格逐片断言（单元格间的「 · 」不再出现）；成功提示改为 toast，测试断言 `notify` 调用；作废成交 / 流水改为一个确认框（理由输入在框内）。
- 未被测试钉住、用户可见的文案变化：多处「—」改为状态文字（未记录 / 无 / 从未运行 / 未指定 / 无分组 / 无记录 / 未提供 / 无详情 / 不适用 / 未成交 / 未出结果 / 未设置 / 未填写 / 未知标的 / 无信号日 / 无失败）；工具运行记录与设置页状态从英文码改为中文标签；知识页头「你自己的经验在 我的知识」「· 返回概念」两句由页签取代；webhook「（已配置）」改为字段说明「已配置」；新增「没有已注册的任务」「已保存」（toast）与各确认框后果句；`FetchDateModal` 首个模式按钮由「默认」改为「昨日（T-1）」（既有 bug：`MODES` 只取初值）；周 / 月复盘卡「生成于」改用 `fmtBeijing`（加「北京」，时间值从 UTC 截取改为北京时间）；对照组「待入场」由合并三列改为三列各写一次；PlanTimeline 新显示「有效」「无差异」、旧版本行「最新第 n 版」链接；「其他触发」空值显示「无标签 / 无规则 / 无简报」；计划历史表分组组头新增可见文本「MU·10-21（5）」「无链（1）」（`DataTable` 组头固定带「（行数）」）。
- 「重新运行 Agent」（运行详情、计划页）与「从此节点继续」新增确认步骤（spec §4.7）；运行详情续跑失败后确认框关闭、原因显示在页面上，重试需重新点按钮（旧版失败后框留着可直接重试）。
- 「复盘」实为 8 个页签（brief 写 5 个）；`FinancialSeries`（转置矩阵）与 `DataBrowse` 原始值表用 `Table.*` 而非 `DataTable`；`VolumeBars` 非放量柱用 `fill-muted-foreground/40`；候选标记用规则色、成交标记用涨跌色；`PlanHistoryTable` 的 `chain_open` 灰底改由分组承担；`RuleBaseline` 当前无调用方，照迁不删。
- 窄屏导航抽屉 `side="top"`（spec「顶部抽屉」原文）；原生 `<select>`（13 个文件共 20 处，测试以 `change` 事件驱动）与原生 `<details>`（21 处，原始数据折叠）保留，各自写类串、未经 `Select` / `Collapsible`。
- 全量 code review 后的修复波（`da76090`）：工具运行记录行用唯一 key（`run_id|started_at`，同一 `run_id` 的续跑记录都显示）；`DataTable` 卡片模式分组列表以组名为 `aria-label`；`Review` 四个提交按钮加 `busy` 与重入保护；复盘的工具调用抽屉与 `FetchDateModal` 关闭后焦点回到打开前的元素；讨论页 `CutoffTime` 空值不渲染；「今日」的 `pctText` 换为 `MetricValue percent`（输出相同）。上述「—」改状态文字（I1）的是未被测试钉住的那批（见上）。
- **仍保留的「—」**（被现有测试钉住、按 §8.1 不改）：`PlanTimeline` 的「执行 —」「影子 —」、标的关键数据 `rs60` 空值、复盘上线前检查列、`ReviewCard` 轨道行、`RunOverview`「输入 — · 产出 —」、`DataBrowse` 原始值单元格、数据页 FRED 任务「下次 —」、`Fundamentals` / `FinancialSeries` 自由现金流、复盘「关联计划：—」与「n=0 · 均 — · 中 — · 正 —」。页面层另有约 76 处直接调用对 `null` 返回「—」的 `lib` 格式化函数（`fmtBeijing`、`wb.pct`、`fmtPct`、`wb.dateTime`、`signedPct`、`rText`），多数字段非空，未逐一迁移。
- `TabNav` 角标的 `aria-label` 在无角色 `<span>` 上（为保持旧测试 `getByLabelText('待审定 N 张')`），非 Chrome 读屏不一定读出；`FetchDateModal` 主按钮仍为「确认」（被现有测试钉住，§8.1 保留，是 §4.7「写动作名」的例外）。

**已知限制**

- spec §10 三条：知识卡 `{@html}` 内容只能套容器令牌；ECharts 工具提示浮层单独映射；lightweight-charts 切主题重建实例。
- **数据页拉取状态仍显示英文码**（`success` / `partial` / `failed` / `skipped`）：旧界面即英文且被 5 处现有断言钉住，按 §8.1 保留；是否作为 Q3 类例外改为中文，待你决定。
- 系统深色且未手动选过主题的用户，首屏先闪浅色再切换（`applyTheme()` 在 `onMount`，`index.html` 冻结不能加内联脚本）。
- 键盘走查测试在 jsdom 里以 `focus()` + `click` 近似（无 user-event）；焦点回退两处测试守护行为但不证明没有修复就会失败（jsdom / Bits 可能自行恢复焦点）。页面未接真实数据目检（开发服务器无后端），留给 Task 9。
- `job_reports.status` 列无 CHECK 约束：工具运行记录遇 `run` 域以外的值时开发模式抛错、生产显示中性灰加原键，Task 9 在真实数据上看一眼。
- Agent 连接「已等待 N 秒」从页面挂载起算，刷新归零。
- 不加 `NODE_OPTIONS=--no-experimental-webstorage` 时，本机 Node 26 下 `theme.test.ts` 等依赖 `localStorage` 的测试会因 Node 自带 webstorage 报错（环境问题，与本版无关）。

### 测试计数

- 前端：vitest 85 个文件 / 999 个测试通过；`svelte-check` 949 个文件 0 错误 0 警告；`npm run build` 通过（输出到 scratch 目录，**`web/dist` 未重建**）。
- 离线 pytest：2503 passed / 976 skipped；一次性库 pytest：3479 passed（本版无 SQL 改动，按 plan 本可跳过，作为回归跑了一次）。
- 退出条件（spec §8.3）核对：页面里无原色类、无 `<style>` 块（除允许列表）、无 `text-2xl` 以上字号、无 `from 'lucide'`；`git diff v2.9.0 --stat -- 'web/src/lib/*.ts'` 只有 `theme.ts`；现有测试删掉的 `expect` 文本只有 Q3 一处；`UiGallery` / `组件画廊` 不在生产构建产物里。

### 未做的事

- **没有迁移、没有 `.env` 变化**；**`web/dist` 未重建、服务未重启、未打 tag `v2.10.0`、未推送 `origin`**——都要你逐项授权（Task 9）。
- 页面目检与验收截图（浅 / 深色）、在真实数据上看工具运行记录的状态值、`Tabs` 原语暂无页面使用、数据页拉取状态的中文化、`ConceptTerm` 搬进 `ui/domain/`、原生 `<select>` / `<details>` 迁到原语，都留待之后。
- 验收记录 `docs/roadmap/v2.10-acceptance.md` 在真实验收开始时新建；不宣称用户验收已过。

## v2.9.0 — 2026-10-30

可执行的计划（[spec](docs/specs/2026-10-30-v2.9-executable-plans-design.md)、[实施 plan](docs/plans/2026-10-30-v2.9-executable-plans.md)）：一只股票同一时间只有一条活跃的计划链，同一标的再次触发不再开第二份互不相关的计划，而是作为 `retrigger` 信号并入这条链、在入场期直接出修订版（可续期）；计划写成可以照着执行的指令——入场类型、开盘落在区间内 / 跳空高开 / 跳空低开各做什么、本计划何时作废、最长持有；「今日」改成入场期 / 持仓中 / 已结束三组的计划表，「标的」页新增计划历史表；取消「采纳 / 否决 / 降级」按钮，采纳由成交关联的计划推出；持仓每个交易日有一行持仓状态（距止损、距 T1、已持有与剩余天数、指令与维持理由），持满最长持有仍没到成本 + 1R 就给退出指令（时间止损），止损触及或失效条件成立进入退出期；新计划与影子口径 `plan-2` / `plan-shadow-2` 与旧 `plan-1` / `plan-shadow-1` 并列、不混算。**有迁移 029**（`trades.plan_id`、`trade_plans` 加两列并替换价位约束、三张新表、`plan_reviews` 约束放宽）。规则 v1、`screen-1`、`regime-2`、`stages-4`、`daily-1`（节点、顺序、行数不变）、`agent-brief-1`、`agent-discuss-1`、模型层一字未动；`agent-2` 的提议、反方、解读提示词逐字不变（逐个比对过大写字符串常量的 sha256，只有 `RETRO_AGENT_VERSION` 与 `REVIEW_VERSION` 两个版本号翻转）。状态：`engineering_done`、`user_acceptance_pending`、`runtime_unverified`——**迁移 029 尚未在真实库执行、服务尚未以 v2.9 代码重启、手动验收未做**（见「待你核查后确认」）。实现偏差与已知限制见下文与 [roadmap](docs/archive/v2/roadmap/roadmap.md)。

### 新增

- **一只股票一条活跃链**（`timeline-2`，`timeline.py`、`plan_review.py`、`service.stage_rules` 的 `plan` 段）：链状态九种（新增 `exiting` 退出期与 `superseded` 的新判定），两个常量 `REVIEWABLE_STATES = (entry, holding)`（复查对象、重新复查）与 `ACTIVE_STATES = (entry, holding, exiting)`（唯一性、再次触发）。`plan` 段在 `chain-open:<标的>` 事务锁下判活跃链，已有活跃链时把这次触发写成 `chain_retriggers` 一行（与规则基线同一事务），不再另开链；`agent` 节点写首版前在同一把锁下二次核对，手动「重新运行 Agent」遇活跃链返回 409 `active_chain`（「`<标的>` 已有进行中的计划链（MM-DD 第 n 版），这次触发已并入」）；已并入的链当天已结束时允许为该单元开新链。同一 session 重跑幂等。
- **再次触发 `retrigger`**：入场期直接走修订（不经单阶段复查），修订可以续期（入场期上限取最近一次触发起 `CHAIN_ENTRY_SESSIONS = 10` 个交易日）；`valid_until` 当天再次触发续期而不是判过期；续期没有模型变化时版本只带宿主 diff（`valid_until`、`entry_cap`）。持仓期与退出期的并入只作为信号和证据，不改价位。
- **执行指令 `entry-1`**（`entry_rules.py`）：入场类型三种（区间限价 `zone_limit`、突破买入 `breakout_stop`、次日开盘 `next_open`）× 开盘三种情形（区间内、高开、低开），逐字文案 `TEXT` 与「本计划何时作废」`void_when`；影子用同一张表（`simulate_entry`），页面写「作废」时影子也不成交。
- **`plan-2` / `agent-3`**：规则基线 `plan-2` 的价位公式与 `plan-1` 逐字相同，多 `entry_type = zone_limit` 与 `max_hold_sessions = 10`；`agent-3` 提议 / 修订多出入场类型（三选一）、最长持有（5–20）、「上一条链」证据块（首版，只含截止时间之前的复查 / 成交 / 日线）与「再次触发」证据块；校验新增 `invalid_entry_type`、`invalid_max_hold`、`stop_lowered`、`stop_above_close`，持仓期修订的价位校验改为 `stop < target1`、止损只能上移且低于最新收盘、入场区间照抄上一版。新增 Agent 原因文案四条（`invalid_entry_type`、`invalid_max_hold`、`stop_lowered`、`stop_above_close`，前后端逐字对齐）及 `active_chain`。
- **成交关联计划与执行状态 `execution-1`**（`execution.py`、`ledger.py`）：`POST /api/workbench/trades` 的 `plan_id`（只能关联成交那一刻的链头：比它旧 → 409 `plan_stale`「成交时这条链已是第 n 版，请关联第 n 版」，成交之后才生成 → 409 `plan_after_fill`）或 `own_plan`（自定计划与成交同一事务写成 `plan-2` / `own`），不选就是「无计划」；`decision_id` 被拒（400 `decision_link_retired`：「v2.9 起成交不再关联决定，请改为关联计划」）。执行状态四种（已采纳 / 有效期外成交 / 等待成交 / 未执行）加链级「已执行」与计划外成交；`GET /api/workbench/trades/linkable-plans` 给出可关联的计划与默认项。
- **持仓状态 `holding_status`**：`review` 节点在复查之后为每个持仓标的每个复查交易日写一行（有链、规则基线 / 自定、无计划、退出期的持仓都有；复查成功、跳过、出错、Agent 不可用都照写；同一 session 先写的为准）：数量、成本、已持有天数、剩余天数、距止损、距 T1、止损模式（`initial` / `trailing`）、指令（继续持有 / 卖一半 / 退出 / 无计划）与维持理由。止损触及、时间止损（持满最长持有且从未收盘到成本 + 1R，T1 之后不再适用）、失效条件成立 → 进入退出期，退出期的链不再复查、只写持仓状态。`agent-review-2` 提示词多出持仓状态块；`condition-2` 新增 `held_sessions_reached`（3–30，宿主为旧链注入 `max_hold` 条件）。
- **「今日」计划表与「标的」计划历史**：`GET /api/workbench/plan-table`（入场期 / 持仓中 / 已结束三组、其他触发；可选交易日，按那个纽约日结束时的记录读）、`GET /api/workbench/holding-status`、`GET /api/workbench/instruments/{ticker}/plan-history`；持仓当天没有 `holding_status` 行时现算并标 `stale`，持仓不会从「今日」消失；命令行 `python -m investment_assistant.workbench.plan_table show --session D`（`plan_views.py`）。迁移 029 未执行时这些接口返回 503「迁移 029 未执行」。
- **备注 `plan_notes`**：`GET|POST /api/workbench/plan-notes`，作用于一条链或一份计划，1–1000 字，只追加（更正与撤回是新的一行指向旧行），只给你自己看，**不进任何 Agent 提示词**（复查、提议、复盘起草、讨论四条路径都核对过）。
- **新口径**：`tracks-2`（实际轨取成交关联的版本，`version_basis = fill`，新增 `merged` 并入其他链 / `voided` 开盘作废）、`discipline-2`（有效期外的关联买入是计划外，不再用旧决定判定）、`retro-2`（复盘卡对象改为有关联成交或有备注的链 / 单元，事实键 `execution`、`holding`，起草多「你的执行」）、`review-8`（`by_execution`、`unplanned_trades`、`units.executed / not_executed`、`plans` 并列 `plans_v1` / `plans_counterfactual` / `shadow_versions`）、`monthly-4`、`agent-retro-2`；`plan-shadow-2`（开盘作废、时间止损第 n 天收盘判定第 n+1 天开盘退出）与 `plan-shadow-1` 同一份新计划并列算（反事实），旧计划只算 `plan-shadow-1`。
- **迁移 029**：见下节。
- **推送**（`notify/templates.py`、`digest.py`）：「新计划」每个入场期链或规则基线三行（入场类型 L–U · 止损 · T1 · 股数 · 有效至；开盘一行；最长持有一行），再次触发写「第 n−1 → n 版」，持仓状态一行（退出行前加「⚠」，维持也推），退出指令单独一条；推送里不再有「未决定单元」。
- **检查与命令行**：`plan_review check` 新增三项——同一标的不得有两条活跃链、并入记录必须有对应处理、每个持仓每天有一行持仓状态；`plan_review chain` 输出 `timeline-2` 的链视图（含并入记录、持仓状态、每版执行状态）。
- **概念卡共 105 张**（新增 `plan.entry_type`、`plan.open_scenarios`、`plan.max_hold`、`agent.retrigger`、`system.active_chain`、`review.holding_status`、`review.execution_status`；`plan.validity`、`plan.own`、`review.chain_state`、`review.version_basis`、`review.control_group`、`review.plan_shadow`、`review.retro_card`、`system.decision`、`system.versions`、`agent.signals`、`agent.invalidation`、`agent.review_when`、`agent.reject_reasons`（33 个原因码）等随改），原理页 15 节（新增「从计划到成交」，相关各节随改）。
- **前端**：`PlanTable`、`PlanRowDetail`、`PlanNotes`、`PlanHistoryTable` 四个组件；「今日」只剩筛选 / 市场环境 / 计划表，没有任何决定按钮；成交录入的「关联计划」下拉；计划页版本链多入场类型 / 最长持有 / 执行状态、并入的单元、退出期与持仓状态历史；复盘卡「你的执行与纪律」；详见 [architecture](docs/archive/v2/architecture.md)。
- **新增版本号**：`timeline-2`、`condition-2`、`plan-2`、`plan-shadow-2`、`agent-3`、`agent-review-2`、`agent-retro-2`、`entry-1`、`execution-1`、`discipline-2`、`tracks-2`、`retro-2`、`review-8`、`monthly-4`（`knowledge.KNOWN_VERSIONS` 另加 `discipline-1`，旧快照与旧卡能引用它）。

### 变更

- **决定退役**：`POST /api/workbench/decisions` 返回 410 `decisions_retired`（「v2.9 起不再记录决定，采纳由成交推导；可在计划上写备注」）；`ledger.record_decision` 删除，`repository.insert_decision` 改名为模块私有的 `_insert_decision_for_tests`，生产代码对 `signal_decisions` 只有 `SELECT`（`tests/test_no_decision_writes.py` 静态守护）；`signal_decisions` 表、旧行、读取接口保留只读，旧决定在旧口径里照旧生效（`closed_by_decision`、`retro-1` 卡、`review-7` 及更早的快照、`tracks-1`）。
- **「今日」**：`GET /today` 的 `units` 与 `extended_units` 恒为空列表并带 `retired: true`，持仓、链、市场、筛选照旧；单元卡数据只剩推送与旧测试用的 `service.unit_cards`。
- **持仓止损 / T1** 取链的最新版（持仓期修订上移的止损随之生效），1R 的风险取第一笔买入关联的那一版的止损；没有链的持仓取成交关联的计划，再无才取旧决定的计划。
- **`get_position` 与持仓事实受截止时间约束**：成交一律用 `list_trades(cutoff=now)` 读（`executed_at` 与 `recorded_at` 都不晚于截止时间，截止之后的作废遮掉），持仓状态块、`holding_status` 的数量 / 成本 / 天数 / 1R 都来自这一份。
- **`daily` 的 029 闸门**：迁移 029 未执行时 `daily` 拒绝运行（`job_reports` 记一行 `error / migration_029_missing`，失败通知一次），续跑（`resume`）同样在 HTTP 预检里直接返回 503，不做半新半旧的降级。
- **复盘卡** `retro-2`：对象由 `repository.retro_subject_keys` 给出；缺迁移 029 的库上 `retro_cards` 命令行退出码 2 并提示 `migration_029_missing`。
- **`GET /api/workbench/review/shadow`（对照组）** 改为 `review-8`：自 `since` 以来的全部单元，没有决定字段，`id = "<ticker>:<signal_date>"`。
- **推理视图**：首版提议的运行多一步「同标的上一条链」（摘要读自 `agent_runs.output.input_digest.previous_chain`）。
- **运维文档**：`docs/operations/migrations-and-backup.md` 加 029 一行与一节，并把 v2.8 遗留的一行里的旧模型层密钥变量字面名改为「旧模型层的密钥变量」（修复 v2.8 提交留下的 `tests/test_no_zhipu_residue.py` 失败，白名单未动）。

### 版本号

- 新增：`timeline-2`、`condition-2`、`plan-2`、`plan-shadow-2`、`agent-3`、`agent-review-2`、`agent-retro-2`、`entry-1`、`execution-1`、`discipline-2`、`tracks-2`、`retro-2`、`review-8`、`monthly-4`。
- 不变：`stages-4`、`daily-1`、`fetch-1`、`siwc-1`、`agent-brief-1`、`agent-discuss-1`、`derivation-1`、`screen-1`、`regime-2`、`context-2`、`backcheck-2`、知识三件。
- 历史保留（旧行、旧快照、旧卡继续引用，不重算）：`timeline-1`、`condition-1`、`plan-1`、`plan-shadow-1`、`agent-2`、`agent-review-1`、`agent-retro-1`、`discipline-1`、`tracks-1`、`retro-1`、`review-7` 及更早、`monthly-3`。

### 迁移 029

`migrations/029_executable_plans.sql`（DDL 按 spec §8 与 §21，可重跑，不改任何旧行；三张新表不进 `WORKBENCH_TABLES`）：

- `trades` 加可空 `plan_id`（指向 `trade_plans`），`trades_link_one_check`（`plan_id` 与 `decision_id` 不并存），部分索引 `idx_trades_plan`。
- `trade_plans` 加 `entry_type`（`zone_limit` / `breakout_stop` / `next_open`）与 `max_hold_sessions`（3–30），两列同有同无（`trade_plans_entry_type_check`、`trade_plans_max_hold_check`、`trade_plans_exec_fields_check`）；价位约束 `trade_plans_levels_check` 替换 017 的 `stop < entry_low`：持仓期 `agent` 行（`inputs.chain_mode = 'holding'`）允许 `stop ≥ entry_low`，其余行照旧拒绝（R3）。
- 新表 `chain_retriggers`（只追加；`UNIQUE (ticker, unit_signal_date)`，`created_at` 用 `clock_timestamp()`）、`holding_status`（只追加；`UNIQUE (ticker, session)`，`stop_mode IN ('initial', 'trailing')`，指令与退出原因互推）、`plan_notes`（只追加；`request_id` 唯一，目标互斥，正文 1–1000 字，`retract` 需要 `supersedes_id`）。
- `plan_reviews` 的 `decided_by` 约束放宽为 `decided_by = 'host' OR review_agent_run_id IS NOT NULL OR revision_agent_run_id IS NOT NULL`（`plan_reviews_agent_run_check`），让入场期 `retrigger` 直接修订的复查行成立。
- 执行顺序：先备份、再执行 029、再重启（D36）；029 未执行时 `daily` 拒绝运行，读接口 503，其余读写成交 / 计划的入口直接报错（见下）。

### 实现偏差与已知限制

下面逐条记录实现相对 spec 的偏差，以及各 Task 审查中接受而没有修的限制（Task 编号对应 plan；spec 与 plan 正文不改）。

**迁移与仓储（Task 1）**

- **R3 的价位 CHECK 用 `COALESCE`**：spec §21.1 R3 ③ 逐字写法里 `inputs->>'chain_mode' = 'holding'` 在 `inputs` 没有该键时为 NULL，CHECK 视 NULL 为通过，漏写 `chain_mode` 的 `agent` 行就能把止损放到入场价之上；迁移用 `COALESCE(inputs->>'chain_mode', '') = 'holding'`，其余相同，对旧行无影响（旧行全部满足 `stop < entry_low`）。因此持仓期修订行**必须**写 `inputs.chain_mode = 'holding'`（`timeline.build_version_plan` 已写）。
- **`_PLAN_COLUMNS` / `_TRADE_COLUMNS` 含 029 的列**：没有 029 的库上，任何读计划（`plan_by_id`、`rule_plan`、`insert_plan` 的 `RETURNING`……）或读成交的入口都会抛 `UndefinedColumn`（500），只有显式闸门的入口（`daily` 的运行 / 续跑、`plan-table`、`plan-history`、`holding-status`、`plan-notes`、`linkable-plans`、带 `plan_id` / `own_plan` 的成交）给 503 `migration_029_missing`，`retro_cards` 命令行退出码 2。这符合 D36（先迁移、后重启，不做半新半旧的降级）；必须先执行 029 再重启。
- **`list_trades(cutoff)` 遮掉截止之后的作废**：截止时间之后才作废的成交在截止时刻仍算有效（`voided_at` / `void_reason` 显示为空）。这超出 brief 的两个时间过滤，但属于截止时间泄漏同一类；代价是读「过去某一刻」时看不到之后才作废的事实，`timeline-2` 一律按这一口径（`timeline-1` 与 `tracks-1` 仍是「任何作废都丢掉」）。
- **`insert_plan` 写执行字段**：`entry_type`、`max_hold_sessions` 随 `_PLAN_COLUMNS` 读写；`store_plan` 判定「同一份」时也比较这两列（旧行两边都是空），所以同一 session 重跑算出不同执行字段是冲突，而不是静默丢弃。
- `chain_retriggers` / `holding_status` 的 `ON CONFLICT DO NOTHING` 重放不比较内容，第一行为准（语义即「同一 session 重跑不重复写」），调用方须以返回行为准；`insert_plan_note` 仓储层懒加载 `ledger.Conflict`（分层上仓储依赖用例层，未理顺）。
- 约束命名：`plan_reviews_agent_run_check`、`trade_plans_levels_check` 等是实现取的名字；两处旧约束按定义用 `DO` 块查找后删除（与 020 同法）。

**纯函数层（Task 2）**

- 版本常量先留旧值、`*_V2` 并存，随各自的接线在 Task 3 / 4 翻转（`PLAN_VERSION`、`TIMELINE_VERSION` 在 Task 3，`SHADOW_VERSION`、`TRACKS_VERSION`、`RETRO_VERSION`、`RETRO_AGENT_VERSION` 在 Task 4）；终态全部为终值，由 `tests/test_v29_version_constants.py` 守护。旧函数保留并与新函数并列（`chain_state` / `chain_state_v2`、`evaluate` / `evaluate_v2`、`actual_track_v2`、`reference_plan_v2`、`build_facts_v2`、`summarize_v2`……），旧调用方行为不变。
- `timeline-2` 的判定函数是新函数 `chain_state_v2(chain, *, session, now, facts)`，不是旧函数的参数；`SIGNAL_KINDS` 仍是 `timeline-1` 的七项，`SIGNAL_KINDS_V2` 多第八项 `retrigger`；`CHAIN_STATES` 是九种（旧 `chain_state` 不会返回新增的两种）。
- `plan_check.check_plan` 缺省 `schema='agent-2'`（`agent-3` 须显式传入），`CHANGE_FIELDS` 是新的九个、`CHANGE_FIELDS_AGENT2` 是旧的七个；`TRACK_STATUS_TEXT`、`retro.BASIS_TEXT`、`DECISION_TEXT` 不变，新状态在 `TRACK_STATUS_TEXT_V2`、`BASIS_TEXT_V2`。
- 条件登记表分两个视图：`CONDITIONS_ALL`（十二种，校验 / 判定用）与 `CONDITIONS`（`condition-1` 的十一种视图，`agent-2` 提示词从它渲染），避免 `held_sessions_reached` 进入 `agent-2` 的提示词（R8 / D16）；`agent-3` 的表由 `CONDITIONS_ALL` 生成，该条件不能出现在失效条件栏。
- 规格只给了示例的文案由实现补定：`next_open` 的作废文案（`首个有效日开盘不在 L–U 内`）、持仓期止损一行（`持仓后：收盘前触及止损 S（T1 之后为移动止损）`）、`OPEN_CASE_TEXT`、`ENTRY_TYPE_TEXT`；计划表有效期 `MM-DD`；「上一条链」证据块里模型写的文字只出现在带前缀的行上（不跟在宿主行后面）。
- `reached_1r` 在第一笔买入到 S 之间的日线有缺口且此前没有收盘到过 1R 时记为未知（缺口可能藏着 1R）；`actual_track_v2` 在单元有效期内没有成交时为 `pending`（R 为空），之后为 `not_executed`（0R）。
- **`next_open` 的第一个有效日没有日线时**，影子返回 `pending`，过了有效期变 `expired`，**不会判作废**（保守处理，页面上不会因缺日线出现「作废」）。
- 时间止损的 1R 以第一笔关联买入所关联版本为准，分次买入时以加权成本计，与影子（单笔成交）不完全一致；`execution.status` 的 `pending` 只在链仍处于入场期时给，持仓期修订版没有关联成交不再显示「入场期，未成交」。

**编排（Task 3）**

- **D1** `agent_runs` 没有 `input_digest` 列（029 不加），「同标的上一条链」证据块的文本与 sha256 存在首版提议运行的 `agent_runs.output.input_digest.previous_chain`；推理视图从这里读，修订运行不显示。
- **D2** `chain_retriggers.created_at` 取本次运行的截止时间（不是数据库时钟）：批处理的 `plan` 段晚于截止时间 T0 才写，用数据库时钟则本次批处理自己的复查窗口 `(since_at, T0]` 永远看不到它。副作用见下条。
- **窄竞态（Task 3 M1，未修）**：批处理在 T0 开始，T0 之后、`plan` 段写入之前用户对同一条入场期链点了「重新复查」（截止时间 t_r > T0），批处理随后写入的并入行 `created_at = T0` 落在窗口 `(t_r, …]` 之外，这次再次触发不会出现在复查里、不会续期，被并入的单元也没有自己的链；`plan_review check` 的并入核对会报出，推送里看不到。
- **D3** 判定「操作性」链状态（复查 / 并入 / 开链）时，复查行按存储原样读（不按 `created_at` 截断），否则 `plan` 段并入的链在复查节点看来已作废；需要回答「过去某刻知道什么」的读者（上一条链证据块）用 `point_in_time=True`。提示词里的 `last_review` 与 `since_window` 仍是 v2.5 起按存储读的老口径。
- **D9 / M5** `plan_review check` 用墙钟判链状态，记录按存储读；持仓状态的核对截止时间取该次运行冻结的 `cutoff_at`（复查行或并入行），没有再回退到阶段开始时刻。
- **D5** `chain_status(..., now=None)` 仍走 `timeline-1`，所有 Task 3 / 4 接线过的调用方都传了 `now`；未接线的读视图在 Task 4 切换。
- **D6** `coverage.covered` 含并入单元（`units = covered + len(missing)` 照旧成立），并入数在 `coverage.merged`，列表在 `merged_units`。
- **D7** 手动「重新运行 Agent」：所并入的链已结束的、从未提议过的并入单元（或二次核对留下 `skipped / active_chain` 的单元）允许重跑（R17）。
- **D8** 规格未写死的文案由实现补定：`agent-3` / `agent-review-2` 提示词、「再次触发」与「持仓状态」证据块、运行一览的新计数句、`check` 的各行、推送里「今日再次触发的规则基线」一行、`rules-1` 简报与 `_why_text` 的措辞。
- **D9** 退出期链的 `detail.signals` 只带再次触发 / 止损 / T1，不读 8-K 与区制；无链持仓的退出指令沿用昨天的状态行；退出期的链不再复查。
- 持仓的 `plan_stop` 现在按 `trades.plan_id` / 链最新版触发，所以这类持仓触及止损的那天除了持仓状态还会产生一个卖出单元（要调用一次解读模型）。
- `service.push` 自己组「新计划」段（`_push_units`），不再从 `today()` 的单元取；规则基线单独成项时也输出三行（链与版本写「规则基线」）。
- 概念卡 `system.active_chain` 的措辞：`superseded` 只会出现在 v2.9 之前的重复旧链上，上线后新链只在无活跃链时才开。

**API 与复盘口径（Task 4，含修复轮更新）**

- **D1** `today` 保留 `holdings` / `chains` / `market` / `screen` / `timeline_ready`，只有 `units` 与 `extended_units` 退役；单元卡构建器作为 `service.unit_cards(now)` 保留（推送与旧测试用）。
- **D2** `retro-2` 对象：只有关联了有效买入或有备注的链 / 单元才出卡，旧的被否决且无备注的单元不再出卡（spec §19 Q6）；未成交单元的结束判定仍用 `plan-shadow-1` 回放（`stop` / `target` / `expired` 保持原义），`plan-shadow-2` 的 `voided` / `time_stop` 不单独结束一张卡；卡的事实里 `shadow` 取主口径（有执行字段的计划为 `plan-shadow-2`）。
- **D3（修复后）** `review-8` / `monthly-4` 的 `plans` 是完整列表：`plan-shadow-2` 的计划用原键，旧影子口径的计划另起一行并在键里写口径（如「规则（旧计划 · plan-shadow-1）」），另有 `plans_v1`、`plans_counterfactual`、`shadow_versions`；`by_pool` 只算 `plan-shadow-2` 的计划、`by_pool_v1` 算其余，统计从不跨口径混算；`by_execution` 多一组 `unplanned`（只数当周计划外买入，没有信号可回放）；`execution[]` 的行带 `unit` 而不是 `decision_id`；自定计划的 `delay_hours` 为空；一笔成交只归给一个单元（链所在单元优先），按 `trade_id` 去重；没有成交的非买入单元记 `not_applicable`，不进已执行 / 未执行，但仍进 `by_rule` 与对照组。`review-7` 及更早的快照不重算。
- **D4** 对照组 `GET /review/shadow` 为 `review-8`（见变更）。
- **D5** `NotReady('migration_029_missing')` 是 `service.Unavailable` 的子类，503 体为 `{"error": "migration_029_missing"}`。
- **D6** 路由补上页面要拆分的句子：409 `active_chain`（日期与版本号取自 `AgentConflict`）、409 `chain_exiting`「这条链已发出退出指令，不再复查」、400 `decision_link_retired`、409 `plan_stale` / `plan_after_fill`。
- **D7** `tracks-2`：已采纳的旧决定但没有成交的单元，实际轨的 `plan_kind` / `plan_id` 为空（`tracks-1` 显示决定的计划）。
- **D8（修复后成立）** `plan_table(session=过去的某天)` 按那个纽约日结束时的记录读，含链、版本、复查、并入与成交；`chain_end` 历史行记在结束日 23:59 纽约；已平仓的无链持仓不进「已结束」组；无链持仓若计划是规则基线 / 自定则出现在持仓组。
- **D9** `service.shadow_outcomes(plan, bars, spy, splits)` 是唯一跑 `simulate_both` 并给结果贴口径标签的地方；链视图、影子页、周 / 月快照、复盘事实、讨论工具都用它。
- **D10** 没有正文的撤回备注存为「（撤回）」（列要求 1–1000 字）。
- 自定计划的最长持有后端限定 3–30（中文提示「最长持有须为 3–30 个交易日」），前端只校验正整数。
- **M7 未修**：`POST /trades` 直接带 `plan_id` 仍可以关联一条退出期链的链头（`linkable-plans` 不列出，API 不拒）；概念卡写的是「退出期的链不再提供关联」。
- `GET /instruments/{ticker}` 仍返回旧 `decisions` 行（只读）；标的页 `focus` 里退出期链的模式显示为「-」；推理视图的「上一条链」步骤只出现在首版提议的运行上。
- 过去某天的计划表按纽约日读，但链 / 版本 / 复查的截断依赖各行的 `created_at`；测试用 `UPDATE … SET created_at` 在一次性库里改时间，生产代码里没有任何 `UPDATE`。

**前端（Task 5）**

- **M9 备注没有更正 / 撤回的页面入口**（接口支持，D25）；备注区只提供添加。
- **M10 计划历史 / 版本链里入场类型、最长持有、执行状态三项并入同一行**（`PlanTimeline` 沿用原有 ` · ` 行布局），不是 spec §10.2 的三个独立列。
- **M11 页面自行取定的文案**（后端已逐字复制）：400 `decision_link_retired`「v2.9 起成交不再关联决定，请改为关联计划」；`tracks-2` 的 `merged`「并入其他链」；`version_basis = fill`「成交时关联的版本」；周度 `by_execution.unplanned`「计划外成交」。410 的两处写法（D24 与 §6.4）取 §6.4 的 API 体：`decisions_retired: v2.9 起不再记录决定，采纳由成交推导；可在计划上写备注`。
- 随决定表单一起删除：`postDecision`、`decisionPayload`、`finalAction`、`Basis`、`ReviewView`、`timeline.staleVersion` / `staleText`、`agent_plan_stale` 的错误分支；`Decision`、`CHOICE_LABELS` 与影子页的「决定」列保留（旧决定只读）。「今日」不再读 `/today` 的单元 / 持仓 / 链 / 扩展池单元（扩展池是计划表的开关），导航守卫（决定草稿）一并删除。
- 行展开里规则基线是一行 `plan-2` 摘要加「展开推导」，不是完整的 `RuleBaseline` 块；版式开关只交换顺序。周度页的对照组页签按 `review-8` 读全部单元；`ReviewCardList` 表头为「你的执行 / 决定」；成交页签不再加载 `/review/shadow`。
- 复盘卡：`FIRST_VERSION_CALCS` 在 Task 4 之后补 `review-8`、`monthly-4`，**另补 `review-7`**（该常量的注释写「review-6 起」，review-7（v2.7）快照此前被漏掉，同属首版口径）。

**发布审查之后（Task 6）**

- 发布前全量审查的 I1–I3、M1–M5、M7、M8 已修（成交日在最后一个有效日仍能关联当日的链头；`retro-2` 卡的 `holding` / `legacy_decision` 事实键前后端对齐，并有静态守护；v2.9 自定计划（`plan-2` / `own`）注册推导；续跑 029 闸门前移到 HTTP 预检；`delay_hours` 为空显示「自定计划，不计延迟」；`retro-2` 卡标「执行回顾」；运维文档对「029 未执行」的说法；备注更正 / 撤回的措辞；`plan_table` 单个标的失败时不留部分行；R5 续期日没有新版时状态写明「本计划已到截止日，明日不可执行」）。
- `ledger.own_plan_signal_date` 的第一级不再接受「仅靠再次触发才能续期的已过期链」（I1 的配套：成交日在 `valid_until` 次日时，该链不再当作成交时刻的活跃链）。
- **已冻结的 `retro-2` 卡**如果是修复之前生成的，其 `legacy_decision` 里没有 `text`（冻结的事实不重算），前端会显示「旧决定：」后只接理由；因为 v2.9 在真实库里还没有运行过，生产上不存在这样的卡。
- 无链持仓与同标的新入场链并存时，`holding_status` 按标的一行（全部未平批次）、`chain_buys` 按链，两边股数可能不同；分次买入使加权成本上移后，`reached_1r` 可能由真变假，时间止损重新生效（R19）。

**spec §16 沿用的已知限制**

- 开盘三种情形是收盘后写好的条件指令，系统不看盘前、盘中价格，你仍要自己挂单。
- 已并入的链当天被宿主作废时，这次触发不会补开新链，要等下一次触发。
- 没有 Agent 链的持仓（成交只关联规则基线，或在 Agent 不可用时成交）不参与复查，只有宿主持仓状态；之后同标的再触发会开一条入场期的新链，与持仓并存。
- 旧 `agent-2` 版本没有执行字段，按默认值显示；旧 `plan-1` 计划不补 `plan-2`。
- 备注不进 Agent；想让 Agent 参考要另写「我的补充」。

### 待你核查后确认

下列都是需要你逐项授权才能做的真实环境步骤（plan 的 Task 7），**一项都没有执行**：

- **只读核查真实库**：写一个只读脚本，上线前列出两类持仓——①持有已超过 10 个交易日、且从未收盘到成本 + 1R 的持仓（旧 `agent-2` 链按 `MAX_HOLD_DEFAULT = 10` 注入时间止损，无链持仓也按 10 个交易日看护，所以**第一次 v2.9 `daily` 就会给这类持仓 `exit / time_stop` 的退出指令，推送里带「⚠」**）；②止损会改按链最新版取的持仓（旧计划止损 vs 链最新版止损，只可能上移）。另列同标的两条以上入场期链（将判 `superseded`）与有旧决定但链仍在入场期的单元。结果写进 `docs/roadmap/v2.9-acceptance.md`。
- **`pg_dump` 全库备份 → 在真实库执行迁移 029 → 重启服务**到 v2.9 代码（029 必须先于重启；重启前核对 `web/dist` 是 Task 6 之后构建的）；不改 `.env`、不改 LaunchAgent plist。
- **手动运行一次 `daily`**（手动运行不推送）核对 spec §14.2 第 1 条（十行、`plan_review check` 通过、计划表三组、没有决定按钮），再逐条做手动验收第 2–6 条；第 7 条（时间止损）与连续交易日的定时验收进入积累。
- 在 v2.8 验收记录里注一笔「v2.9 于 <日期> 上线」、更新 roadmap 状态、打 tag `v2.9.0`、推送 `origin`，各自单独授权。
- 验收记录 `docs/roadmap/v2.9-acceptance.md` 在真实验收开始时新建；不宣称用户验收已过。

### 验收

- 测试（发布提交的最终树上重跑）：离线 pytest 2488 passed / 976 skipped；一次性库 pytest 3464 passed；前端 `npm test` 730 passed（53 个文件），`npm run build` 通过；`knowledge check` 通过（105 张卡、原理页 15 节）；`tests/test_docs_links.py` 通过。
- **验收未开始**：迁移 029、备份、重启、手动验收、tag 与推送都需要你逐项授权，均未执行（计划的 Task 7）；退出条件按 spec §14（手动 `daily` 十行且 `check` 通过、计划表三组、成交关联计划得到「已采纳」、再次触发不开第二条链、持仓每天一行持仓状态、时间止损在影子与页面上一致，以及连续交易日的定时观察）逐条待积累。

## v2.8.0 — 2026-10-29

每日工作流（[spec](docs/specs/2026-10-29-v2.8-daily-workflow-design.md)、[实施 plan](docs/plans/2026-10-29-v2.8-daily-workflow.md)）：三个定时任务（`filings`、`workbench`、`workbench_review`）合成一个任务 `daily`，北京 11:30（周二至周六）跑一次，十个节点串成一张 LangGraph 图；每个节点把检查点写进库，失败的运行能从失败节点续跑而不重跑已完成的部分；外部请求统一套紧超时与有限重试；新增运行详情页（节点状态图、逐目标拉取明细、结果明细）；Agent 改走你自己的 ChatGPT 订阅（Sign in with ChatGPT），删除智谱。**有迁移 028**（一张表加列改约束、三张新表、调度行合并），规则 v1、`screen-1`、`regime-2`、`plan-1`、`agent-2` 的结构与校验、四张子图的提示词与判定一字未动。状态：`engineering_done`、`user_acceptance_pending`、`runtime_unverified`——**迁移 028 尚未在真实库执行、服务尚未以 v2.8 代码重启、尚未登录 ChatGPT；你的核查脚本 `scripts/siwc_precheck.py` 尚未运行，模型层按 spec §8.7 先走路径 A，其请求形状全部未对真实 ChatGPT 端点验证**（见「待你核查后确认」）。保留偏差与已知限制见下文与 [roadmap](docs/archive/v2/roadmap/roadmap.md)。

### 新增

- **每日工作流图 `daily-1`**（`workbench/workflow.py`、`tasks/daily.py`）：十个节点 `sec_monitor → collect → screen → regime → signals → plan → agent → review → push → retro`，`DailyState` 只放可序列化的值；`service.run_batch` 按阶段边界逐行搬移成九个阶段函数（`stage_collect` …），`run_batch` 保留为依次调用它们的薄壳。节点出错 60 秒后自动重试 1 次（一个节点无论重试几次只写一行证据，第一次的异常记在 `detail.retry`），再失败停在该节点，其后未执行的节点各记 `skipped: upstream_failed`，运行状态 `failed`。
- **检查点与续跑**：每个节点结束时整条链的状态写入检查点（`thread_id = run_id`，与 Agent 子图共用同一组检查点表）；`POST /api/jobs/daily/resume`（`{run_id, node}`）前置检查同步做、不写任何行，通过后后台从检查点续跑。拒绝条件：`not_failed`、`node_mismatch`（只能从检查点的下一个节点续）、`resume_window_closed`（已过下一交易日纽约 17:00）、`daily_running`。续跑沿用原运行的 `session` 与 `cutoff_at`；`collect` / `sec_monitor` 续跑时丢掉来源时间晚于 `cutoff_at` 的资讯与 SEC 申报，日线只写到 `session`。续跑只给重新执行的节点追加 `attempt = n + 1` 的行，已完成节点不重跑、不追加。
- **外部请求策略 `fetch-1`**（`data/fetch_policy.py`）：连接 10 秒、总 30 秒（每次尝试是硬截止，含读响应体），网络错误 / 5xx / 429 按目标重试 2 次（退避 2 秒、8 秒），4xx 不重试；yfinance 走 `curl_cffi` 会话并外加 35 秒线程硬截止；`data/http.get_json`、`bars`、`news`、`earnings`、`macro`、`collect`、SEC 监控与下载器、Discord 推送都经它。推送只在请求未发出（连接失败）与 429 时重试，5xx 与读超时不重试（避免重复消息）。`data_fetch_runs.detail.targets` 新增逐目标明细（`target`、`ok`、`attempts`、`error_kind`、`http_status`、`reason`、`rows`、`latest`、`elapsed_ms`；每族至多 600 项，超出的只记数量）。SEC 监控的列表改为逐只查询，一只失败不再拖垮整族。
- **运行详情页** `#tools/runs/<run_id>`（`GET /api/runs/{run_id}/detail`，`workbench/run_detail.py`，全程只读）：顶部一句话与元信息、同一次运行的多次执行、十个节点的状态图；选中节点后分「做了什么 / 拉取详情 / 结果详情」三块（结果每类至多 200 项并给真实总数），底部折叠原始摘要；失败节点上有「从此节点继续」，`agent` 节点被 `max_attempts` 或出错挡住的单元有「重新运行 Agent」；`stages-1/2/3` 的旧运行也能打开（只读，标明没有拉取明细）。运行记录的「详情」改为链接，旧 JSON 弹窗删除；「今日」顶部的运行一览多一个「运行详情」链接。
- **Agent 连接**（设置页，`workbench/agent/chatgpt.py`、`workbench/agent_connection.py`，协议 `siwc-1`）：OAuth 动态注册 + PKCE + 本机回环回调（每次登录在 `127.0.0.1` 起一个独立的短期监听，10 分钟过期），之后服务自己刷新（构造模型前取令牌，剩余不足 300 秒且已过 `earliest_refresh_at` 就在行锁内刷新、先写库再使用）；设置页可选模型（清单来自 `GET /models` 里 `visibility == "list"` 的项，不写死模型名）、断开（远端撤销 + 本地清空令牌）。接口：`GET|DELETE /api/settings/agent-connection`、`POST .../login`、`POST .../cancel`、`GET .../models`、`PUT .../model`。令牌不进日志、不经任何接口回显（`authorize_url` 本身带 `client_id`、`ext_agent_host_id` 与重连时的 `login_hint`，这是授权协议的一部分）。
- **模型层路径 A**（`workbench/agent/model.py` 的 `SiwcChatOpenAI`）：`ChatOpenAI` Responses 模式的子类，`_get_request_payload` 里把 system 消息搬进 `instructions`、删去被禁字段（`agent/chatgpt.py` 的 `FORBIDDEN_FIELDS`，16 项，逐字按 spec §8.5）、按 `TOOL_SHAPE` 改写工具形状，`output_version='v0'` 让 `content` 保持字符串；四张子图与 `ToolNode` 循环不改。被禁字段、工具形状、请求覆盖、是否从 system 取 `instructions` 都是常量，且可用 `WORKBENCH_CHATGPT_EXTRA_FORBIDDEN`、`WORKBENCH_CHATGPT_TOOL_SHAPE`、`WORKBENCH_CHATGPT_OVERRIDES`、`WORKBENCH_CHATGPT_INSTRUCTIONS_FROM_SYSTEM` 在核查期覆盖（调用时读取，不在设置页环境变量列表里）。错误分类（spec §8.8）：额度用尽 → `quota_exhausted`（立即停止本次其余单元）；未连接 / 令牌失效 / 资格类错误 → `not_connected`；所选模型不可用 → `model_unavailable`；连接丢失发生在节点中途时，本节点其余单元记 `skipped`（`agent` 与 `review` 都适用）。
- **迁移 028**（`migrations/028_daily_workflow.sql`，DDL 逐字按 spec §10）：`stage_runs` 加 `attempt` 列、`job` 增加 `daily`、`stage` 增加 `sec_monitor`、唯一键改为 `(run_id, stage, attempt)`；新表 `agent_connections`（单行、**可改**，存令牌）、`agent_connection_events`（只追加，不含令牌）、`agent_attempt_resets`（只追加）；`scheduled_jobs` 删 `workbench`、`workbench_review`、`filings` 三行、插入 `daily`（`11:30`、`2-6`、`Asia/Shanghai`，`enabled` 取原 `workbench` 行的值）。新表不进 `WORKBENCH_TABLES`；`agent_connections` 只由 `agent_connection.py` 读写，`repository.py` 里没有这个表名；`agent_attempt_resets` 在 `repository.py` 里只有 `INSERT` / `SELECT`。迁移 028 未执行时 `daily` 拒绝运行（`job_reports` 记一行 `error / migration_028_missing`，失败通知一次），不做半新半旧的降级。
- **接口**：`GET /api/runs/{run_id}/detail`；`POST /api/jobs/daily/run`、`POST /api/jobs/sec_backfill/run`（`POST /api/workbench/run` 等价于手动 `daily`）；`POST /api/jobs/daily/resume`；`POST /api/workbench/agent-runs/rerun`（`{ticker, signal_date, mode, request_id, origin}`）；上面的 Agent 连接六个接口。`GET /api/jobs/reports` 不变，`task` 筛选多了 `daily`、`sec_backfill`。
- **CLI**：`python -m investment_assistant.tasks.daily run [--manual] | resume --run-id R --node N | show --run-id R`；`python -m investment_assistant.workbench.agent_connection status`；`derivation stages` 认 `stages-4` 与 `attempt`；`python -m investment_assistant.tasks.filings --from D --to D` 仍是 SEC 回补入口（任务名 `sec_backfill`）。
- **一次性核查脚本** `scripts/siwc_precheck.py`（说明见 [scripts/README.md](scripts/README.md)）：由你在运行服务的这台 Mac 上执行，把末尾的「核查摘要」追加到 spec §17；脚本不打印令牌，邮箱脱敏。
- **3 张概念卡与原理页新一节**：概念卡共 98 张（新增 `system.fetch_policy`、`system.daily_workflow`、`system.agent_connection`；`system.stages`、`system.stage_status`、`system.versions`、`system.push`、`agent.reject_reasons` 随改），原理页 14 节（十个节点各一节，新增 `#stage-sec_monitor`）。
- **新增版本号**：`stages-4`、`daily-1`、`fetch-1`、`siwc-1`；不变：`stages-1/2/3`（历史）、`agent-2`、`agent-review-1`、`agent-brief-1`、`agent-retro-1`、`agent-discuss-1`、`retro-1`、`review-7`、`monthly-3`、`screen-1`、`regime-2`、`plan-1`。
- **前端**：运行详情页（`RunDetail`、`NodeGraph`、`FetchTable`）、「设置 → 系统」下的 Agent 连接（`AgentConnection`）、计划页的「重新运行 Agent」、任务中心对 `daily` / `sec_backfill` 的处理；详见 [architecture](docs/archive/v2/architecture.md)。

### 变更

- **批处理 `stages-3` → `stages-4`**：十个节点，`sec_monitor` 排在最前、`retro` 挪到 `push` 之后（复盘出错不再耽误当天的计划推送）；每次运行每个节点有一条**有效行**（最大 `attempt`），共 10 条；`derivation.RUN_STAGE_COUNT` 为 `{stages-1: 7, stages-2: 8, stages-3: 9, stages-4: 10}`，`derivation stages` 与运行一览按有效行判断。纽约休市日 `push` 之前的八个节点记 `skipped: not_trading_day`，`push` 只发休市一行，`retro` 仍判断周 / 月复盘是否到期；`retro` 在周 / 月复盘到期与交易日的复盘卡两件都不做时才记 `skipped: not_due`。
- **`retro` 节点合并两件事**：复盘卡（v2.7 的检测、冻结、起草，交易日照做）和周 / 月复盘与复盘推送（原 `workbench_review`，到期才做；手动运行只生成不推送）。
- **每日推送**：复盘卡行改为「复盘卡 上次推送后新增 a · 待审定 b」（a、b 都为 0 时不出）；有族 `partial` / `failed` 时多一行「数据：<族名> <ok>/<target>（<n> 个目标没拿到，见运行详情）」，至多 3 个族。手动运行不推送。
- **`duration_ms`** 改为节点真实起止之差（含重试等待与外部请求的全部等待）；v2.7 及以前的 `duration_ms` 只累计部分子步骤，旧行不改。
- **运行状态口径**：由节点有效行推出——有 `failed` 为失败（`job_reports.status = 'error'`），否则有 `partial` / `degraded` 为降级，否则成功。不再用「`summary.errors` 非空即 error」，所以单个标的取数失败是降级而不是失败。
- **Agent 预算**：`agent` 与 `review` 共用一个 1500 秒窗口，起点改为 `agent` 节点开始（采集节点的重试不再挤占 Agent）；`retro` 在推送之后，复盘卡起草用自己的 1500 秒窗口；续跑时窗口从续跑的那一次执行重新计。`BATCH_BUDGET_S` 与整批预算降级删除。
- **`max_attempts` 可重跑**：手动重跑先在 `agent_attempt_resets` 追加一行重置记录，之后计数只数重置之后的运行；旧的 `agent_runs` 行不改不删。
- **区制异常改为停链**：`regime` 失败 → 重试 → 仍失败则停在 `regime`，可续跑（v2.7 是记失败后继续出计划）。
- **`signals` 与 `plan`**：同一循环在 `signals` 节点里跑完，`plan` 节点只做校验与记行；循环里计划部分的耗时记在 `detail.timings.in_signals_ms`。
- **新增失败通知**：运行状态 `failed` 时向「每日计划」频道发「⚠️ 每日工作流失败」，每次执行至多一条，降级不发；ChatGPT 连接失效时另发「⚠️ ChatGPT 连接已失效」（一次失效只发一次，重新连接后再失效才再发）。推送类别键 `workbench`、`workbench_review`、`filings` 保留（每日计划 / 复盘 / SEC 披露），已保存的通知设置原样可用。
- **`filings` 并入 `daily`**：SEC 监控是第一个节点（`sec_monitor`）；回补改为只手动的任务 `sec_backfill`（不进 `scheduled_jobs`，必须带日期，范围 ≤ 92 天）。Data 菜单各族的 `job` 一律为 `daily`。
- **`data/http.get_json`** 的 `timeout` / `max_retries` / `backoff_seconds` 参数删除，改为 `policy` / `target` / `sleep` / `redact`（返回形状不变）；`PriceConfig.max_retries` / `backoff_seconds` 删除（旧配置文件里的这两个键被忽略）。`EdgarSource.discover` 返回 `(metas, errors)`。
- **`AgentSettings`**：`base_url` 字段删除，`api_key` 变成脱敏用的当前 access token，新增 `connection`；`WORKBENCH_AGENT_PROVIDER` 只认 `chatgpt` / `off`（缺省 `chatgpt`）；`WORKBENCH_AGENT_DAILY_TOKENS` 保留。
- **新增 Agent 原因文案**（前后端逐字对齐）：`not_connected`「ChatGPT 未连接」、`model_unavailable`「所选模型不可用」。

### 删除

- **三个定时任务** `workbench`、`workbench_review`、`filings`：代码（`tasks/workbench.py`、`tasks/workbench_review.py` 与 `filings` 的任务外壳）和 `scheduled_jobs` 行都删除；`job_reports.task` 里它们的历史值保留只读，任务筛选里带「（v2.7 及以前）」。
- **智谱**：`DEFAULT_MODEL`、`DEFAULT_BASE_URL`、`TEMPERATURE`、`REQUEST_TIMEOUT_S`、`CLIENT_RETRIES`、`_body_code` 的智谱分支、`provider` 缺省 `'zhipu'`，以及对 `ZHIPU_API_KEY`、`WORKBENCH_AGENT_BASE_URL`、`WORKBENCH_AGENT_MODEL` 的读取全部删除；文档与概念卡（`agent.reject_reasons` 的额度错误码段）改写为 ChatGPT；全部测试夹具里的 `provider: zhipu` / `glm-*` 换成 `chatgpt` / `legacy` / `gpt-test`（`tests/test_no_zhipu_residue.py` 固定残留只剩 spec §9 的允许清单）。已有 `agent_runs` / 计划 / 简报里的 `provider = 'zhipu'`、`model = 'glm-5.3'` 保留只读，页面照常显示当时的模型名。**真实 `.env` 里的几行（三个旧变量、`WORKBENCH_AGENT_PROVIDER`）由 Task 7 经你授权后编辑，尚未改。**

### 修正

- **讨论回答与复盘卡草稿被误判为格式错误**（v2.7 验收发现，原「未发布」条目）：真实模型的多段回答在 JSON 字符串里直接写换行，严格的 `json.loads` 拒绝字符串内的控制字符，回答被记为 `answer_malformed`（复盘卡草稿同理会记为 `retro_malformed`）。`growth_check` 在严格解析失败时改用 `strict=False` 再读一次（仍只剥一层代码围栏），不是 JSON 的输出照旧作废；`agent/citations.py` 不动，提议 / 复查的解析不变。
- **Discord 网络错误文本不再含 webhook 令牌**（本版实现中发现并修）：网络类错误只保留异常类型名，`/api/webhooks/<id>/<token>` 无论出现在哪都被改写。
- **一个旧测试曾向真实 FRED 主机发过请求**（用的是假密钥，已改为离线）。

### 与 roadmap 文字的差异（spec §21）

- 新版本 `stages-4`（roadmap §4.6 写的是 `stages-3`、每次恰好 10 行）：`stages-3` 在真实库里已是「九行、`retro` 在 `push` 之前」，同一个版本两种形态无法守护。
- `retro` 同时做复盘卡与周 / 月复盘，只有两者都不做才 `not_due`（roadmap 只提到原 `workbench_review`）。
- 续跑追加 `attempt` 行，「10 行」= 10 条有效行（只追加：失败行与 `upstream_failed` 行不能改写或删除）。
- 纳指 100 成分的日线与财报日也在 `collect` 采，`screen` 为新入池成员补采资讯与 SEC。
- v2.7 的 `workbench` 任务其实不发失败通知（`notify=False`），本版新增「每日工作流失败」通知。
- 路径 A 是 `ChatOpenAI` 的**子类**改写请求体（`disabled_params` 关不掉 `_default_params`，也不会生成 `instructions`），另需 `output_version='v0'`；工具形状按官方限制页要求用 namespace 或 `additional_tools`。
- 被禁字段按官方限制页补全为 16 个；错误分类另加 `invalid_refresh_token`、`token_expired`、`invalid_id_token`、`account_mismatch` 与资格 / 授权类错误码。
- 只有一个 LaunchAgent（服务本身），三个任务是 `scheduled_jobs` 行，由进程内调度线程执行，plist 不需要改。
- `GET /models` 的返回形状是 `models[]` 的 `slug` / `display_name` / `visibility`（不是 OpenAI 平台的 `data[].id`）。

### spec §22 各项实际按什么实现

- **Q1 十个节点的版本号**：`stages-4`（D1），未沿用 `stages-3`。
- **Q2 复盘卡移到推送之后，推送行怎么报**：改报「上次推送后新增 a · 待审定 b」（D3）。
- **Q3 续跑后的「10 行」**：追加 `attempt` 行，10 条有效行（D9）。
- **Q4 `signals` 与 `plan` 的拆分**：同一循环在 `signals` 里跑完，`plan` 只做校验与记行。
- **Q5 区制计算异常**：停链，可续跑。
- **Q6 SEC 回补**：保留为只手动的 `sec_backfill`。
- **Q7 失败通知**：只在运行失败时发、每次执行一条，降级不发。
- **Q8 ChatGPT 令牌的存放**：本机数据库明文（与 `.env` 同级，备份文件同样含令牌，`pg_dump` 的备份放 `.local/`）。
- **spec §17 的核查路径**：核查结果仍为空，模型层按 §8.7 第 4 条走**路径 A**（`SiwcChatOpenAI`），未经核查，可经 `WORKBENCH_CHATGPT_*` 覆盖；若核查表明 A 不可行，退到路径 B（`openai` SDK 薄客户端）是另一次改动。

### 实现偏差（相对 spec）

**图与任务外壳**

- **概念卡编号 `system.fetch_policy`**（spec 写 `data.fetch_policy`）：卡 id 必须以已有分组开头（`family` / `indicator` / `rule` / `plan` / `agent` / `review` / `system`），新增 `data` 组要改 `knowledge.py` 与前端知识页，超出本版范围。张数（98）与节数（14）不变。
- **原理页编号**写成「⓪ SEC 监控 … ⑧ 复核与推送、⑨ 复盘」（spec 文字写「⑩ 复盘」，会留下一个空的 ⑧）。
- **`headline`** 写「10 月 29 日的运行在「…」失败…」，“日”与“的”之间没有空格（照 spec §7.1 的示例，不是模板字面）；“（已自动重试 1 次）”只在该行有 `detail.retry` 时出现（`Unavailable` 类失败不重试）。
- **`workflow.node`** 返回的是 `run(state, ctx)`（spec 写 `Callable[[DailyState], dict]`），`ctx` 由 `build_graph` 绑定；`NodeContext` 多出几个可选字段，`workflow.run` / `resume` 多一个 `config=`。
- **SEC 列表**逐只查询（D21）；所有股票都失败或 `company_tickers.json` 都拿不到时，`summary` 沿用 v2.7 的整次调用形状（`filings = {'error'}`、`sources.sec = ['all']`），部分失败给 `<T> sec: …` 行。
- **`screen` 的补采也补财报日**（spec 只写资讯与 SEC）：v2.7 的逐只循环就补，`test_invalid_screen_plan_stays_read_only` 依赖计划读到刷新后的财报日；本轮范围步骤没拉到的成员（保留 / 锁定、不在今日范围内）也在这里补日线。
- **`collect` 的 `outputs.data_fetch_runs`** 列出五个族行（v2.7 为四个）；`detail.earnings` 多 `items`，续跑时多 `after_cutoff_dropped = {news, sec}`；检查点状态多一个 `refs['collect']['families']`（推送数据行用）。
- **复盘卡行**：此前没有任何 `daily` 推送时 `a` 记 0；`service.push` 在 `retro_counts` 为 None 时退回 `summary['retro']`，所以 v2.7 的外壳与手动推送不变。
- **运行一览的“续跑”计数**按每个阶段的行数推出（`stage_runs_for_session` 不读 `attempt`，`repository.py` 超出该 Task 的边界）。
- **`run_now`** 先预检 `daily.is_running()` 再回 409 `daily_running`；预检本身连不上库时退回直接提交。
- **v2.7 的任务层**留在 `tests/_v27_task.py`（`stages-3` 记录器 + `run_batch` 外的推送），让 v2.7 阶段证据测试保持价值；产品代码里已没有它。
- **没有进图的失败**（`migration_028_missing`、`checkpoint_unavailable`）的失败通知不带「未执行」「续跑」字段，失败节点写“每日工作流”；定时运行撞上 `daily_running` 只写一行 `error`，不发通知。
- **失败节点信息**（`failed_node` / `failure`）放在 `summary` 里，不进检查点状态（spec 写在检查点状态里）。
- **spec §15 的笔误**：休市时“`push` 之前九个节点 `skipped: not_trading_day`”，十个节点的图里 `push` 之前只有八个（九是 `stages-3` 的数法），实现为八个。
- **`first_failure_since_ok` 与连接通知**：“一次失效只通知一次”的基线是“最近一次真正发出的连接通知的 `checked_at`”（`summary.connection_notice.checked_at`），不是上一次执行的起止时间；通知发送失败会在下一次执行重试。
- **`AgentSettings.model`** 在没选模型时是 `''`（不是 `None`），因为 `agent_runs.model` 是 `NOT NULL`；`unavailable_reason` 把 `None` 与 `''` 同样当作 `model_unavailable`。
- **`QUOTA_CODES`** 没有删，留作只含 `subscription_sharing_usage_limit_exceeded` 一个码的集合（spec §9 说删）。
- **`usage {cached_input_tokens, reasoning_tokens}` 没有写进 `agent_runs.detail`**（spec §8.7 最后一段）：字段名要等 §17 C3 核查确认，写错代价比晚写大；每日 token 上限仍只按 input + output 计。`usage_metadata` 里已有这两个数。
- **`model_unavailable` “只通知一次”没有实现**（§13.2 没有对应模板）：`not_connected` 通知一次；`model_unavailable` 只体现为该节点其余单元 `skipped` 与运行详情里的原因。
- **登录与断开的前置判断在路由里**：`login_pending`（已有未完成的登录）与断开时的 `not_connected` 由路由按 `status_view()` 判断，因为 `agent_connection.start_login` 会取消进行中的登录并重开，自己不会抛 `login_pending`。
- **断开会清空 `subject` 与 `email`**（保留 `host_id`、`client_id`、所选模型）：spec §19“换账号要断开再连”优先于 devkit 的“断开保留 subject”；代价是 `account_mismatch` 只对 `needs_login` 状态下的重新登录生效。
- **刷新的瞬时失败**（网络、5xx、响应无效）只写 `last_error`，不写 `refresh_failed` 事件（事件只给需要重新登录的失败）。
- **授权码换令牌与刷新**的 POST 用 `FetchPolicy(retry_post=False)`（只在请求没发出或 429 时重试）：刷新令牌被消耗后重放会得到 `refresh_token_reused`。
- **只覆盖同步路径**：`SiwcChatOpenAI` 只改了 `_get_request_payload` 与 `_stream`；`_astream` / `ainvoke` 本版不用，没有改写。
- **请求改写多出几步**（`agent-track.md` 已写）：回放的 `function_call` 输入项去掉 `id`（`store=False` 时 `fc_…` id 无处可查），在 `namespace` 形状下加 `namespace: 'workbench'`。
- **`insert_stage_run` 在迁移 028 之前也能写第一次尝试**：第一次尝试不写 `attempt` 列（v2.7 的 `workbench` 行），`daily` 行与 `attempt > 1` 的行总是写。
- **`Tally.targets_truncated`** 是被省略的目标**个数**（整数），不是布尔；旧的 `Tally.ok / fail`（没有尝试记录）在 `detail.targets` 里 `attempts = null`、失败行 `elapsed_ms = null`、`error_kind = 'other'`；成功项 `http_status` 记 200。
- **Discord 推送**通过 `fetch_policy.policy_session` 发送（重试规则同上）。
- **SEC 监控**的逐只错误记进 `summary` 并让覆盖状态为 `partial`，通知标题为「SEC 采集部分失败」。
- **下载器模式忽略续跑截止**：`sec_downloader` 没有可比较的接受时间，续跑时不按 `cutoff_at` 过滤（已写入 `sec-filings.md`）。
- **`push` 在已发出消息之后抛异常**时，续跑会重发（极小概率；不做幂等去重）。
- **`daily_ready` 检查的是 `agent_attempt_resets`，不是 `agent_connections`**（spec §10.4 写后者）：spec §10.2 不许 `repository` 出现 `agent_connections` 这个表名（连接表只由 `agent_connection` 模块访问），而三张表在同一个迁移里一起建，检查任何一张都等价。
- **`detail.abandoned_threads`**（spec §18 风险表的应对：检查点线程超过续跑窗口后可清理）本版没有实现，旧线程留在检查点表里。

**接口与前端**

- **API 错误体**沿用现有契约 `{"error": "<代码>"}`（字符串 `detail`），不是 plan 写的 `detail = {code, message}`——全局异常处理器会把非字符串 `detail` 压成字面 `"error"`；中文文案在前端（`RESUME_ERROR_TEXT` 等）。
- **ChatGPT 相关错误只带安全码**：`not_connected` 409、`model_not_listed` 400、`migration_028_missing` 503、其余（`discovery_failed`、`callback_port_unavailable`、`network_error` …）502；`psycopg.OperationalError` / `service.Unavailable` → 503。
- **`POST /api/workbench/agent-runs/rerun` 对 `propose` 是同步的**（和现有 `POST /api/workbench/agent-runs` 一致，请求在 Agent 运行结束后才返回，`run_id` 是新的 `agent_runs` id；spec §5.7 写“后台”）。`origin` 只接受 `run_detail`、`plan_page`、`api`（缺省 `api`，其它值 400）；成功文案「已重置尝试次数，并已重新运行」（`interpret` 只重置，由同一交易日的续跑或手动运行执行——下一次定时运行已是新的交易日，`_session_units` 只收 `signal_date == session` 的单元，所以不会用到；文案「已重置尝试次数，同一交易日的续跑或手动运行时执行」）。只有 `propose` 在任何 `daily` 执行持有锁期间被拒（409 `daily_running`），`interpret` 照常重置；同一个 `request_id` 已被另一个单元用过时，回的是那次的重置（`replayed: true`），不运行。
- **`running` 状态的判定多一条**：spec 写“有行但本次执行没有报告、且锁被持有”；实现里本进程正在执行的运行（`daily.executing`）也算 `running`，否则续跑后第一行写入之前页面会写 `failed`。仅有 `daily-…` id 而没有行的运行，在本进程正执行、或锁被持有且 id 时间戳在 [−6 小时, +5 分钟] 内时给一个十个节点全是 `pending` 的骨架，否则 404。
- **崩溃中断的运行不可续跑**：进程在节点中途被杀、没有失败行时 `resume.reason = 'interrupted'`（“这次运行中途中断，只能整次重新运行”）；续跑自己中途死掉而留下的陈旧 `upstream_failed` 行同样按 `failed` 显示。`resume.reason` 取值：`not_failed`、`stage_failed`、`interrupted`、`resume_window_closed`、`daily_running`、`checkpoint_unavailable`。
- **`resume.deadline_at`** 在详情接口里是 UTC ISO（spec 示例），`resume_check` 内部是纽约 ISO。
- **`can_rerun`**：该单元同模式的最新一次运行以 `error` 或 `skipped / max_attempts` 结束、其后没有重置记录、是 `daily` 运行且没有在运行、没有任何 `daily` 执行持有锁。
- **详情接口的结果细节**：`sec_monitor.total` 是该行的 `output_count`（存储的列表截到 50 项）；`push.channel` 取自最新报告的 `summary.push.channel`；`retro` 的周 / 月复盘行 `status` 是该次 tick 的 `action`（`generated`、`reused`、`already_sent`、`reminded`、`error`，`before_start` / `waiting` 不列），`push` 为 `sent` / `manual_run` / 未发送的原因；被跳过的节点（`push` 除外）不列结果。接口返回的是代码，中文由前端翻译（`RETRO_STATUS_TEXT`、`RETRO_PUSH_TEXT`）。
- **没有迁移 028 的旧库**：详情页读 `stage_runs_for_run`（没有 `attempt`），不查检查点，旧运行照常显示。
- **「Agent 连接」放在 `#settings/system` 页**「系统」之下，侧栏没有新增设置子页（spec §8.9 写“「系统」之后”）。
- **计划页的重跑入口复用现有 `GET /api/workbench/agent-runs?since=`**（新增前端函数 `getAgentRuns`）：接口没有标的筛选，所以会取回自信号日以来的全部运行，计划越老越多；取回失败时入口静默隐藏。
- **`sec_backfill` 在任务中心只有“按日期运行”**，没有“立即运行”（它必须带日期）；弹窗默认选项发 `{date: <本地昨天>}`。`scheduled_jobs` 里没有 `sec_backfill` 行，所以 `services/jobs.scheduled_jobs()` 为 `REGISTRY` 里这类只手动任务补一行 `{name, manual_only: true, enabled: false, next_run_at: null, time_local / weekday_mask / timezone: null, description}`——**`/api/jobs` 多了一个键 `manual_only`**，前端据此渲染“只手动”。
- **续跑与运行的请求体带 `request_id`**（项目规则：写操作带 `request_id`；spec §5.4 只列 `{run_id, node}`）；登录与选模型同样。
- **`RUN_ID_RE` 不含 `us-research-…` 的运行 id**（`^[a-z_]+-\d{14}-[0-9a-f]{8}$`，spec §7.1 自己的两处描述互相矛盾）：这类运行在运行记录里没有“详情”链接（显示“—”）。
- **前端文案**：409 `node_mismatch` 显示失败节点的中文标题；运行记录的状态用本地映射（`error` → 失败），不认识的状态原样显示；“拉取详情已列全”“没有失败的目标”“已有未完成的登录，可先取消再重新连接”等不在 spec 里的短句是实现者所写。
- **`window.open(…, 'noopener')`** 按规范返回 `null`，所以没有“弹窗被拦截”的回退链接；被拦截时再点“连接 ChatGPT”，后端回 409 `login_pending`，页面刷新状态并给出“取消”。

### 已知限制

- spec §19 逐条沿用：整链没有硬上限（每个外部请求 ≤ 2 分钟、Agent + 复查 ≤ 25 分钟、复盘起草 ≤ 25 分钟，但目标数 × 最坏耗时理论上仍可很长，例如 Yahoo 全面超时时 230 个目标 × 100 秒；v2.9 的备源解决“整族不可用”）；续跑只能从失败节点开始；财报日续跑时取到的是当时的值；检查点不清理；`attempt` 之前的旧运行（`stages-1/2/3`）没有续跑、详情页只读；单账号（换账号要断开再连，断开时远端撤销可能无法确认）；ID token 不验签名（只从令牌端点经 TLS 取得）；**令牌明文存于本机数据库，数据库备份文件同样含令牌**；周日、周一不运行，这两天到期的复盘推送顺延到周二 11:30；推送前的复盘卡改到推送后，当天新建的卡在下一次推送里报数；链内拆股换算、`get_trigger` / `get_position` 不按截止时间过滤等沿用 v2.5–v2.7 的已知限制。
- **模型层未经真实端点验证**：路径 A 的请求形状（被禁字段、`TOOL_SHAPE` 缺省 `namespace`、回放 `function_call` 去 `id`、`Accept: text/event-stream`）全部是读文档与源码后的推断，在你运行核查脚本、第一次真实登录前，Agent 单元可能因请求被拒而记 `model_error`。
- **近过期的令牌仍会交出去**：`earliest_refresh_at` 未到而令牌只剩几秒时，仍把这个令牌交给模型调用（与 spec 字面一致）；一次模型调用最长 180 秒，可能中途 401、被分类为 `not_connected`，让本节点其余单元被跳过。
- **崩溃中断的运行不可续跑**，只能整次重新运行；CLI 续跑（另一个进程）和“上一次续跑被拒、留下 `job_reports` 行”的运行，在第一条新行写入前显示 `failed`；进程被杀的运行在另一次执行持有锁时显示 `running`。
- **yfinance 共享 `curl_cffi` 会话下的 cookie / crumb 行为未在真实 Yahoo 上验证**（评审者按源码推断：不重复取 crumb、财报日历不爆 401 / 429、`hard_deadline` 放弃线程后会话仍可用）——真实验收时观察。
- **`model_unavailable` 不单独通知**（见上）；**重跑 `propose` 同步执行**，请求会等到 Agent 运行结束。
- **`usage` 两个数字没有落库**（见上）。
- **`GET /api/runs/{id}/detail`** 对每个 `stages-4` 运行都会开一个短连接查 `daily.is_running()`，每 5 秒轮询没问题，列表里批量调用则不行；它读检查点用第二个数据库连接（读，但不在只读事务里）。
- **`read_for_settings()`（即每次 `settings_from_env()`）有写库副作用**：会顺带收尾过期的登录与残留的待定状态。
- **`INVESTMENT_ASSISTANT_FAULT_ONCE=<节点>`**（造一次节点失败的演练开关）只用于 Task 7 的手动验收，用后要从 `.env` 里删掉。
- **`service.agent_window` 的 25 分钟预算**只用假时钟测过，真实 25 分钟的运行没有观察过。

- **节点自己记了 `failed`、链却跑完的运行不能续跑**（终审 I1，按 spec §5.2 / Q5：只有 `regime` 从“记失败继续”改成“停链”）：Discord 推送没发出去（推送策略不重试，例如 502）、整段 `review` 失败、复盘卡或筛选整段失败时，运行状态是失败，但检查点没有下一个节点，所以没有“从此节点继续”：失败通知里没有「续跑」一行（「未执行」也只在其后有节点时写），运行详情的 `resume.reason = 'stage_failed'`（「这一步记了失败但链已跑完，不能续跑；需要时整次重新运行」）。只有停链的失败可以续跑。推送失败是其中最常见的一种；若以后要让它可续跑，做法是在推送未发出时抛出、交给节点重试与续跑（改变 v2.7 的阶段语义，未做）。
- **没连过的 ChatGPT 不通知**：从未连接或已断开（`disconnected`）时每天的 `agent` / `review` 静默跳过（摘要里只记 `not_failed`），只有连接丢失（`needs_login`）才发「ChatGPT 连接已失效」，设置页显示当前状态（spec §15 写“通知一次”，这里只覆盖失效）。
- **上游节点停链而且没有续跑时，当天的周 / 月复盘不会生成或推送**（`retro` 在链尾），顺延到下一次运行（v2.7 的 `workbench_review` 是独立任务，不受上游影响）。

### 待你核查后确认的模型层行为

下列全部是假设，没有一条对真实主机观察过；在 `scripts/siwc_precheck.py` 的“核查摘要”追加到 spec §17 之后，逐项对照，不符的改对应常量或环境变量（C 编号是 spec §17 的行）。

- **C1 登录与令牌**：发现文档的 `issuer == https://auth.openai.com`，`authorization_endpoint`、`token_endpoint`、`jwks_uri` 在该源下；第一次登录的回调里带 `client_id`（动态注册），没有也没有已存的 `client_id` 时登录以 `registration_incomplete` 失败；换码返回 bearer、数字 `expires_in > 0`、`access_token`、`refresh_token`、字符串 `scope`、`id_token`（换码时必须有）；**刷新必须返回新的 `refresh_token`**（缺了按 `invalid_token_response` 处理、保留连接——若发行方不滚动刷新令牌，这条要放宽）；刷新的 `scope` 可省（沿用旧的）、`id_token` 可省（有则 `sub` 必须与已存一致）；`earliest_refresh_at`：数字按 epoch 秒、字符串按 ISO-8601，缺失或无法解析视为无限制；ID token 的 `iss` 完全等于发行方、`aud` 含 `client_id`（列表有多项时要 `azp == client_id`）、`exp` 为数字（容差 5 秒）、登录时 `nonce` 与我们发出的相同、有 `sub`、可选 `email`；`scope` 含 `chatgpt.tokens.use.direct` 且刷新后仍保留；撤销接口接受表单 `token`、`token_type_hint=refresh_token`、`client_id` 并以 HTTP 200 应答；`refresh_expires_at` 每次刷新记为“现在 + 30 天”（实际期限并未从响应里读）；浏览器回调的 `Host` 是 `127.0.0.1:<端口>`（`localhost:<端口>` 会被拒）。
- **C2 模型清单**：`GET {资源}/models` 返回 `{"models": [{slug, display_name, visibility}]}`，可选项 = `visibility == "list"`；只要有一项缺 `slug` / `display_name` 就整表按 `invalid_model_catalog` 失败（同 devkit）；设置页默认不预选任何模型（没选时 Agent 记 `model_unavailable`），该预选哪个 slug 由你定。
- **C3 最小原生调用**：SSE 序列以 `response.completed` 结束，其中 `response.status == "completed"`，`usage` 含 `input_tokens`、`input_tokens_details.cached_tokens`、`output_tokens`、`output_tokens_details.reasoning_tokens`、`total_tokens`；失败以 `response.failed`（`response.error.code`）或 `error` 事件（`code`、`message`）到达——langchain 把两者都变成一个文本以 `code='…'` / `<code>: ` 开头的 `ValueError`，`_failure_from` 据此还原错误码（langchain 改了这段文本，流内的额度识别会悄悄退化成 `model_error`；经 openai 异常的 HTTP 状态错误不受影响）；流在没有 completed 事件时结束记 `stream_interrupted`，`response.incomplete` 记 `response_incomplete`。
- **C4 被禁字段**：16 个 `FORBIDDEN_FIELDS` 正是路由会拒绝的全部，且我们发出的其它字段都不被拒；缺省只发 `model`、`input`、`instructions`、`store: false`、`stream: true`、`tools`。待确认：是否必须显式发 `parallel_tool_calls`（若是则 `REQUEST_OVERRIDES = {"parallel_tool_calls": false}`），`include` / `reasoning` 以后能不能加，`max_retries=0` 加 `Accept: text/event-stream` 在 SDK 上是否没问题。
- **C5 工具形状**：缺省 `namespace`。要确认 `top_level` / `namespace` / `additional_tools` 里哪一种能让模型调用工具；`namespace` 项里的工具名是否以 `function_call.name`（加 `namespace`）返回；第二轮回放 `function_call`**不带 `id`**、`namespace` 形状下**带 `namespace: "workbench"`**，再加 `function_call_output` 是否被接受（这是核查脚本 `_tool_request` 没覆盖到的一处假设——脚本回放的是模型原样返回的内容，含 `id`）；`additional_tools` 下回放的调用是否不需要 namespace。
- **C6 请求体与输出形状**：LangChain 发出的输入项形如 `{"role": "...", "content": "<str>", "type": "message"}`（devkit 不带 `type`）；`role: developer` 被接受；system 文本必须放进 `instructions` 且 `instructions` 被接受；改写不动其它输入项（`function_call`、`function_call_output`、带 `output_text` 内容且无 `id` 的 assistant `message`）；`output_version='v0'` 时消息内容是 `[{'type': 'text', 'text': …, 'index': 0}]` 块（我们拼成一个字符串，只含工具调用的轮次得 `''`）；`tool_calls[].id` 是响应里的 `call_id`；若有 reasoning 项，因 `store=False` 又没有 `encrypted_content`，回放时被丢掉。
- **C7 额度用尽**：目前只匹配错误码 `subscription_sharing_usage_limit_exceeded`，真实的 HTTP 状态与响应体形状没观察过；第一次真实出现时按 `agent_runs.detail` 追加到 spec §17 C7。
- **401 的处理**：模型调用上的 401 记为 `not_connected`；令牌签发（登录或刷新）后 600 秒内的 401 直接标 `needs_login`，更老的令牌 401 先把 `access_expires_at` 置为现在、让下一次取令牌时强制刷新——这套“新令牌才算失效”的阈值（`FRESH_TOKEN_S = 600`）也是推断。
- **HTTP 客户端**：一个共享的 `httpx.Client`（环境里的代理变量生效；LaunchAgent 不继承 shell 的代理，要在 `.env` 里写），剥掉 `OpenAI-Organization` / `OpenAI-Project` 头，`httpx.Timeout(180, connect=10)`。

### 验收

- 测试：离线 pytest 2248 passed / 827 skipped；一次性库 pytest 3075 passed（终审修复 fed0a02 之后）；前端 `npm test` 672 passed（51 个文件），`npm run build` 通过；`knowledge check` 通过（98 张卡、原理页 14 节）。
- **验收未开始**：迁移 028、`pg_dump` 备份、`.env` 改动、重启、ChatGPT 登录、tag 与推送都需要你逐项单独授权，均未执行（计划的 Task 7）；退出条件（连续 10 个交易日 `daily` 定时运行无需人工介入、手动造一次节点失败并续跑成功、没有外部请求超过 2 分钟、你任选 3 天打开运行详情都能回答“哪个数据源对哪个目标没拿到、为什么、卡在哪个节点”、Agent 单元在 ChatGPT 上完成且通过同一套引用校验、智谱引用已删）逐条待积累，验收记录 `docs/roadmap/v2.8-acceptance.md` 在真实验收开始时新建；不宣称用户验收已过。

## v2.7.0 — 2026-10-29

我的成长闭环（[spec](docs/specs/2026-10-28-v2.7-growth-loop-design.md)、[实施 plan](docs/plans/2026-10-28-v2.7-growth-loop.md)）：每笔结束的交易有一张复盘卡（事实冻结、Agent 只讲不算、你审定后教训才进「我的知识」）；「我的知识」只追加、带版本，Agent 用 `get_knowledge` 按截止时间读、用 `know:<id>@<版本>` 引用，引用记入 `knowledge_citations`；每条知识有表现回看；计划卡与「标的」页有「就此计划讨论」；周度复盘多一段「你学到了什么」。**有迁移 027**（十一张新表、放宽三处约束），规则、`plans.py`、`tracks.py`、`discipline.py` 等口径一字未动。状态：`engineering_done`、`user_acceptance_pending`（迁移 027 已于 2026-10-29 在真实库执行、服务已以 v2.7 代码重启；手动验收与定时验收进度见 [v2.7 验收记录](docs/archive/v2/roadmap/v2.7-acceptance.md)）。保留偏差与已知限制见下文与 [roadmap](docs/archive/v2/roadmap/roadmap.md)。

### 新增

- **迁移 027**（`migrations/027_growth_loop.sql`）：十一张只追加的表——`review_cards`（冻结事实，`event_key` 唯一）、`review_card_drafts`、`review_card_verdicts`、`discussions`、`discussion_turns`、`knowledge_items`、`knowledge_versions`、`knowledge_status`、`knowledge_drafts`、`knowledge_draft_verdicts`、`knowledge_citations`；放宽 `agent_runs.mode`（`retro`、`discuss`）、`agent_tool_calls.phase`、`stage_runs.stage`（`retro`）三处约束。不改任何旧行；新表不进 `WORKBENCH_TABLES`，`repository.py` 对它们只有 `INSERT` / `SELECT`；缺表时按 `growth_ready` 降级（见「变更」）。
- **复盘卡**（`retro.py` 纯函数 `retro-1`、`retro_cards.py`）：批处理新阶段 `retro` 检测最近 5 个交易日内结束的对象（持仓平仓、计划止损、到 T1、过期未成交、作废），把十个事实（`subject`、`plans`、`bars_after`、`shadow`、`tracks`、`decision`、`trades`、`discipline`、`reviews`、`knowledge`）冻结成一张卡；Agent（`agent-retro-1`）把事实讲成一段话并起草至多 3 条教训，每次批处理至多起草 5 张、每张至多 3 次尝试；你审定（教训逐条采纳可改、不采纳或另写）后，一个事务写审定、决定与「我的知识」第 1 版。卡的状态由记录推出：已审定、待审定、起草失败、起草中。
- **我的知识**（`knowledge_items.py`、`my_knowledge.py`，`my-knowledge-1`）：写、改（新版指向旧版，带 `base_version` 防覆盖）、归档与恢复（状态记录）、看版本；挂靠概念与标的（合计 1–10）；来源有三种——你直接写、复盘卡教训、讨论草稿；截止时间之前的可见性由纯函数 `visible` 决定。
- **知识表现回看**（`knowledge_perf.py`，`knowledge-perf-1`）：每条知识 → 引用它的计划 → 这些计划的影子结果，每个单元一个样本（取最早引用这条知识的那一版）；样本量常驻，少于 5 个（`review.SHADOW_MIN_SAMPLE`）不给均值。
- **就此计划讨论**（`discussion.py`，`agent-discuss-1`）：从计划卡、计划页、「标的」页的计划行与复盘卡进入；讨论专用只读工具三个（`get_plan_context`、`get_derivation`、`get_agent_run`），只读本单元；回答带引用，可附一条经验草稿（新写或改写），草稿在「我的知识」草稿队列里审定；可把某一轮存为补充信息（走 v2.5 的用户输入）。回答在后台生成，历史最近 10 轮 / 12000 字。
- **Agent 工具 `get_knowledge`**（第十个工具，排在 `get_concept` 之后）：`get_knowledge(ticker=None, concept=None)`，都不给时返回索引（至多 50 条），结果至多 10 条；只返回截止时间之前写入、未归档的版本，**不返回任何草稿**。
- **`know:<id>@<版本>` 引用**与 `knowledge_citations`：Agent 引用必须带版本，且只能引用本次运行 `get_knowledge` 返回过的版本；`know:` 不能单独支撑价位。引用记录与计划同事务写入、同生共死；作废、`pass`、被反方丢弃的条目不留引用。计划卡、版本链与推理视图显示「引用的知识」，之后有新版时注明。
- **接口**：`GET/POST /api/workbench/review-cards`（列表、卡页、`/verdict`、`/redraft`）、`/api/workbench/my-knowledge`（列表、新建、条目页、`/versions`、`/status`、`/performance`）、`/api/workbench/my-knowledge-drafts`（列表、`/verdict`）、`/api/workbench/discussions`（建讨论、列表、讨论页、`/turns`）；已有 `today`、`chains`、`instruments/{ticker}`、`weekly-reviews` 只新增键（`knowledge_refs`、`knowledge`、`stats.learning`）。写接口都带 `request_id` 幂等。
- **两个 CLI**：`python -m investment_assistant.workbench.retro_cards detect | draft | show | check`；`python -m investment_assistant.workbench.my_knowledge show | check-cutoff`（把「知识受截止时间约束」的校验落到真实数据）。
- **页面与组件**：「复盘」页新增「复盘卡」页签（`#review/cards[/<id>]`，`ReviewCardList`、`ReviewCard`、`CardVerdictForm`）；「知识」菜单新增「我的知识」（`#knowledge/mine[/new|/drafts|/<id>[@<版本>]]`，`KnowledgeList`、`KnowledgeItem`、`KnowledgeForm`、`KnowledgePerformance`）；`KnowledgeRefs`、`RefList`、`DiscussButton`；讨论页 `#discuss/<id>`（`Discussion`）；「标的」页新增「我的知识」一段；周度复盘新增「你学到了什么」。详见 [architecture](docs/archive/v2/architecture.md)。
- **7 张概念卡与原理页新一节**：概念卡共 95 张（`review.retro_card`、`review.retro_end`、`review.knowledge_performance`、`review.weekly_learning`、`system.my_knowledge`、`system.discussion`、`agent.knowledge_citation`），原理页 13 节（新增 `#stage-retro`）。

### 变更

- **批处理 `stages-2` → `stages-3`**：第 8 个阶段 `retro`（在 `review` 与 `push` 之间），每次运行 9 行 `stage_runs`；`derivation.RUN_STAGE_COUNT` 为 `{stages-1: 7, stages-2: 8, stages-3: 9}`，`derivation stages` 按各运行自己的版本核对。迁移 027 未执行时本次仍按 `stages-2` 记 8 行（没有 `retro` 行），`summary['errors']` 加 `growth: migration_027_missing`。运行一览有 `retro` 一行；每日推送概览末尾加「复盘卡 新增 a · 待审定 b」（都为 0 时不加）。
- **周度复盘 `review-6` → `review-7`**：快照新增 `stats.learning`（纪律合计、本周结束的复盘卡、本周被检验的知识），周度推送新增字段「你学到了什么」；旧快照不重算，页面标「该快照没有『你学到了什么』」。
- **Agent 工具由九个变十个**：`agent/tools.py` 只新增 `get_knowledge`、`Recorder.knowledge_refs` 与 `TOOLS` / `TOOL_DESCRIPTIONS` 条目，原有九个工具的函数、参数、返回值、标的范围与截止时间不变；讨论专用三个工具不在批处理的工具表里（批处理的五个阶段绑定的工具不含它们，有测试固定）。
- **三份 `agent-2` 系提示词新增一段**「可以读、怎么引用知识」；`agent_version`（`agent-2`、`agent-review-1`、`agent-brief-1`）**不变**（spec D19 / Q3）。
- **Agent 新作废原因**：`retro_malformed`、`lesson_invalid`、`answer_malformed`、`migration_027_missing`、`discussion_busy`、`draft_exists`，前后端文案逐字对齐。
- **前端路由**：`review`、`knowledge` 两个区保留第一段之后的路径（`#review/cards/12`、`#knowledge/mine/3@2`）；新增区 `discuss`。
- **新增版本号**：`stages-3`、`retro-1`、`agent-retro-1`、`agent-discuss-1`、`my-knowledge-1`、`knowledge-perf-1`、`review-7`；不变：`agent-2`、`agent-review-1`、`agent-brief-1`、`condition-1`、`timeline-1`、`plan-shadow-1`、`tracks-1`、`monthly-3`、`knowledge-1`。

### 修正

- **`agent-1` 计划的推理视图入口**（原「未发布」条目，随本版发布）：计划页对 `agent-1` 计划另显示一段「Agent 推理」，标「agent-1（旧版，不参与复查）」，「看推理过程」按推导输入里生成它的运行 id 展开推理阅读视图（展开时才请求）。此前 `agent-1` 计划卡只出现在它自己交易日的「今日」页上，历史 `agent-1` 计划的推理视图只能从接口读到（v2.6 验收 §14.2 第 5 条发现）。

### 与 roadmap 文字的差异（spec §20）

- 周度复盘在代码里已是 `review-6`（v2.5 D17），本版升 `review-7`，不是 roadmap 写的「`review-5` 加一段」。
- 新增批处理第 8 个阶段 `retro`（共 9 行），`stages-2` → `stages-3`；roadmap §4.5 没有提到阶段。
- `get_knowledge(ticker=None, concept=None)`：两者都不给时返回索引（roadmap 写 `get_knowledge(ticker 或 concept)`）。
- 「未审定的草稿不被任何 Agent 引用」加强为「不被任何 Agent 工具**返回**」；唯一例外是你在讨论里主动选「就这条改写」，草稿作为你的发言附件让模型看到原文，仍不可引用（D24）。
- 概念卡新增 7 张（共 95 张）。
- 按用途给工具（D20）先在 `graph.py` 实现，运行时仍是智谱；v2.8 的按用途配置沿用同一切分。

### spec §21 各项实际按什么实现

- **Q1 未决定、也无成交的单元**：不出卡（只对你做过决定的单元与你的成交出卡，D1）。
- **Q2 上线前已结束的交易**：不自动补卡，只检测最近 5 个交易日内结束的；要补时授权后用 `retro_cards detect --since <日期>` 手动补（D6）。
- **Q3 提示词加知识一段是否升 `agent_version`**：不升，仍为 `agent-2` / `agent-review-1`（D19）。
- **Q4 讨论的截止时间**：提问时刻（D22），提示词要求区分「当时」与「之后」。
- **Q5 表现回看的样本单位**：每个单元一个，取最早引用这条知识的那一版（D26）。
- **Q6 起草预算**：每次批处理至多 5 张，计入每日 token 上限，排在复查之后（D9）；复查阶段把额度用尽写进 `summary.review.quota_exhausted`，复盘阶段据此跳过。

### 实现偏差（相对 spec）

- **检测窗口**：「最近 5 个交易日」实现为含 `S` 的 5 个交易日（`retro.window_start(S)` = `S` 前推 4 个交易日）；`retro_subjects` 按**结束日**窗口选对象。
- **持仓单元的参考计划**：取买入所挂靠的决定的 `plan_id`，否则取决定链上最近一个带 `plan_id` 的决定（spec D3 的读法；起初只看最新决定，终审后改）。`no_plan` 只在平仓落入检测窗口时计数；既无任何计划又无买入的单元不计 `no_plan`；`retro_cards check` 把窗口内无参考计划的结束单独列出并判不通过。`_freeze` 对有买入的单元把参考计划标为 `executed`，所以最新决定是否决的持仓单元也看得到执行的是哪份计划。
- **决定文案**：`retro.DECISION_TEXT` 多一个兜底键 `accept`（「采纳」，已采纳但执行的计划不明），前端 `DECISION_TEXT` 同键（含 `accept_agent_legacy`）；Agent 版本依据文案 `retro.BASIS_TEXT` / 前端 `VERSION_BASIS_TEXT`（决定时看到的版本 / 首版）。
- **`filled` 字段**：复盘卡列表行、`learning.cards`、卡视图 `card` 多一个 `filled`（有成交），实际一栏写法为：有 R 写 R，有成交无 R 写「无 R（计划外）」，未成交写「未成交」——spec 模板没有区分「计划外成交」。
- **409 `draft_decided`**（spec 无此码）：同一请求 id 之外的请求再审定已审定的教训草稿；前端补了文案。讨论草稿视图带 `target_current_version`，目标条目已有新版的改写草稿只能不采纳（409 句子 + 前端禁用；显式给 `base_version` = 最新的 API 调用仍可采纳）。
- **`get_agent_run` 不读 `retro` 运行**：spec §8.3 只排除讨论自己的运行；D11 与 roadmap「未审定的草稿不被任何 Agent 引用」优先，复盘运行的输出就是卡的草稿。
- **`get_plan_context` 对卡只给 `reviewed` / `not_reviewed` 两态**：其余状态要读草稿表，而没有任何工具路径可以碰草稿表（有测试固定）。
- **价位上的 `know:` 不入 `knowledge_citations`**：027 的 `field` CHECK（spec §6.2 的字段集）没有对应值，只记论点、失效条件、变化说明、反方、风险、复查。
- **`tools.py` 超出「只新增」**：`ToolContext` 改为 frozen dataclass，`tools.call(tools=)` 多一个可选参数（默认 `TOOLS`，行为不变）；`tasks/workbench.py` 按 `growth_ready` 选 `stages-3` / `stages-2` / `stages-1`（D22）。
- **讨论轮次**：`run_id` 存的是该轮回答将携带的 Agent 运行 id（表只追加，不能提交后再回填），后台执行器 id 只在进程内登记；没有会话级 advisory lock，忙判定在事务锁内加进程内登记，写回答时在同一把锁下复核；重放同一 `request_id` 时返回的 `run_id` 含义与首次不同（202 `{status: 'replayed'}`）。
- **跳过的起草**：写一行 `review_card_drafts`（`skipped`，无 `agent_runs`），同一原因重复不再写；`draft_cap` 不写行。
- **`check` 的「事实已变化」**按冻结当时的交易日与已知日线重算，成交作废等后来的变化只列出、不算失败。
- **LangGraph checkpoint 表**每次 Agent 运行也写行，spec 说讨论「只写四类行」不含它们。
- **前端**：「存为补充信息」按钮放在每个已写入的用户轮次上（`request_id` 靠这一轮的 `request_id` 派生，发生在该轮写入之后），不放在输入框旁；纪律标记写「死扛亏损」（沿用现有 `FLAG_LABELS`，spec §4.7 写「死扛」）；「标的」页只给 Agent 计划行和「计划价位」行加讨论按钮。
- **文档与测试**：`tests/test_dashboard_frontend_source.py` 的路由断言随新增的 `discuss` 区更新。

### 已知限制

- **「采纳 + 买入、后又否决」的卡**：卡存在，叙述与成交都在，但 `facts.tracks.actual` 仍按 `tracks-1`（最新决定无 `plan_id` → 显示未执行，0R）；`tracks.py` 本版不动。要改需另发 `tracks-2`。
- **冻结事实不随后来的更正变化**：卡页与 `retro_cards check` 标「事实已变化」，不重建、不删卡。
- **纪律「待定」**：过早止盈要卖出后 20 个交易日才能判定，以卡页「现在的判定」为准。
- **成交在建卡后被作废**，同一单元可能因此变成另一种结束，按 `event_key` 规则出第二张卡（spec §15）。
- **未决定的单元没有复盘卡**（D1）；上线前的旧交易默认没有卡（D6）。
- **讨论截止时间是提问时刻**：回答可能用到计划之后的数据，只靠提示词区分「当时」与「之后」；`get_trigger`、`get_position` 不按截止时间过滤（v2.6 已记录）；讨论不流式，一次回答可能要一两分钟，服务重启会中断正在进行的回答。
- **表现回看只看影子**，不区分你是否真的照着做；**知识正文是纯文本**；概念卡改版后，知识上挂的 `concept:<id>@<旧版>` 不自动更新。
- **跳过的起草行占用尝试序号**；列表与卡页的读取是逐卡逐条的（N+1，个人规模可接受）；`retro_subjects` 不剪枝未成交且无卡的单元，每个单元每次要做一次影子重放。
- **没有迁移 027 时**周度「你学到了什么」读作「没有结束的交易」，而不是「未启用」。
- 链内拆股的换算问题沿用 v2.5。

### 验收

- 测试：离线 pytest 1863 passed / 616 skipped；一次性库 pytest 2479 passed；前端 `npm test` 586 passed（45 个文件），`npm run build` 通过；`knowledge check` 通过（95 张卡、原理页 13 节）。
- **验收未完成**：2026-10-29 经用户授权，先 `pg_dump` 全库备份，再在真实库执行迁移 027 并以 v2.7 代码重启服务；§16.2 手动验收第 1、4、6 条通过、第 5 条部分通过，其余待出现，§16.3 定时观察待积累（[v2.7 验收记录](docs/archive/v2/roadmap/v2.7-acceptance.md)）；不宣称用户验收已过。

## v2.6.0 — 2026-10-28

看懂系统（[spec](docs/specs/2026-10-28-v2.6-understanding-the-system-design.md)、[实施 plan](docs/plans/2026-10-28-v2.6-understanding-the-system.md)）：在系统里就能看懂系统——「今日」顶部的运行一览、仓库内版本化的 88 张概念卡与「知识」菜单、「原理」页、四处就地展开、Agent 推理阅读视图、「标的」页四段重排与 K 线标注，另给 Agent 加只读工具 `get_concept`。没有迁移、没有新表，不改任何计算口径与 Agent 流程。状态：`engineering_done`、`user_acceptance_pending`（2026-10-28 以 v2.6 代码重启服务，§14.2 手动验收见 [验收记录](docs/archive/v2/roadmap/v2.6-acceptance.md)；复查推理、`get_concept` 调用、成交标注与你的使用验收待积累）。保留偏差与已知限制见下文与 [roadmap](docs/archive/v2/roadmap/roadmap.md)。

### 新增

- **知识层 `knowledge-1`**（`workbench/knowledge.py`）：读 `docs/knowledge/` 的概念卡（YAML 头 + Markdown 正文）与原理页，一次报出全部校验问题，渲染时关闭原始 HTML、卡内只许指向其它卡的链接，进程内缓存（失败不缓存），不依赖数据库。引用形式 `concept:<id>@<version>`，指向旧版时只返回当前版并带 `stale` 说明。
- **88 张概念卡与一页原理**：七组（数据族 / 指标 / 规则 v1 / 规则计划 `plan-1` / Agent 计划 / 复盘 / 系统运作），`docs/knowledge/principles.md` 讲系统每天怎么跑；守护测试（`tests/test_knowledge_catalog.py`）让规则、原因、状态、工具、版本号变了而卡没改时失败。
- **四个只读接口**：`GET /api/knowledge/concepts`、`GET /api/knowledge/concepts/{ref}`、`GET /api/knowledge/principles`（加载失败 503 并列出问题）；`GET /api/workbench/run-overview?session=&run_id=`（只读 `stage_runs`，每个阶段一句话、原因文案、跳过 / 失败 / 降级明细）；`GET /api/workbench/agent-runs/{run_id}/reasoning`（把一次 Agent 运行按阅读步骤摊开）。读取都在只读事务里，不调用外部数据源。
- **两个 CLI**：`python -m investment_assistant.workbench.knowledge check | show <ref>`（校验目录、打印一张卡）；`python -m investment_assistant.workbench.run_overview --session D [--run-id R]`。
- **Agent 工具 `get_concept`**：只读，不碰数据库、不受截止时间约束，结果带 `point_in_time: false` 与说明，调用记录里标出这一例外；是否调用由模型决定。
- **页面与组件**：侧栏「知识」菜单（「概念」浏览与单卡页、「原理」页）；`ConceptTerm`（就地展开概念摘要）挂到「数据」菜单、推导视图、计划卡、规则基线、版本链、「今日」决定表单与「标的」页；`RunOverview`（「今日」顶部运行一览）；`AgentReasoning`（计划卡与版本链的「看推理过程」）。
- **依赖**：显式声明 `markdown-it-py`、`PyYAML`。

### 变更

- **「标的」页重排**为四段：信号与计划（含「今天该关注什么」）、关键数据、背景、历史；K 线图叠加标记（触发候选、成交）与计划价位线段（最多 12 份计划，作废的计划淡显）。`GET /api/workbench/instruments/{ticker}` 新增 `focus`、`key_data`、`context`、`plans`、`chart.annotations` 等键，旧键的含义不变。
- **`candidates` 读取窗口变宽**：`GET /api/workbench/instruments/{ticker}` 的 `candidates` 读取起点由信号日前 120 天改为图上第一根日线（约 250 个交易日），「标的」页历史时间线的候选随之变长。
- **Agent 工具由八个变九个**：`agent/tools.py` 只新增 `get_concept`，原有八个工具的函数、参数、返回值、标的范围与截止时间不变。
- **`RULE_LABELS` / `labels.RULE_TEXT`**：补 `plan_stop`（计划止损）、`plan_target`（计划目标），前后端逐字对齐，由跨语言测试固定。

### 实现偏差（相对 spec）

- **`instruments` 接口多两个键**：`holding`（该标的的「今日」持仓行：当前止损、T1、距离；§9.1 要求持仓块展示当前止损与 T1，而 §9.4 没有对应的键）与 `errors`（`{块: 原因}`，说明新增块里哪个读取失败）。
- **运行一览不把进行中的运行当记账失败**：所选运行没有最后一个阶段的行、且缺的阶段都在最后一个已记录阶段之后时，缺的阶段写「还没有这一阶段的记录：运行可能仍在进行，或中途被终止」，摘要加「（可能仍在运行）」。
- **`plans` 的候选窗口**：列出 `valid_until` 不早于图上第一根日线日期的计划，因此包含今天生成、有效期从下一交易日开始的计划，而不是严格按「与窗口相交」；这份计划在第一根有效日线出现之前没有图上线段（§9.3 字面）。
- **财报日当天的焦点句**：写「今天发布财报（日期）」，不写「0 个交易日后…」。
- **运行一览的带参数原因**也翻译已知参数：`agent_unavailable: not_configured` 写成「Agent 不可用：未配置」。
- **推理视图**：反方没有调用工具或没有运行时写「反方没有调用工具 / 反方没有运行」，而不是 §13 的通用句「没有记录到工具调用」；推导校验未通过时，结果步骤里显示 `consistency_error`。
- **知识接口 503 时 `ConceptTerm`** 保留 ⓘ 按钮并显示「概念卡暂不可用：<原因>」（按 §6.1；§13 写的是只显示文字）。
- **卡校验比 §4.1 更严**：引用式链接、带标题的链接、原理页 `##` 标题里的链接一律不通过；原理页一节最多链接一个阶段。
- **`get_concept` 的 id 带 `@版本`时忽略版本**，返回当前版的卡。
- **卡内容以代码为准**（文档或 spec 与代码不一致处）：ATR14 是最近 14 个真实波幅的简单平均（只读 15 根日线），不是 Wilder 平滑；`get_trigger` / `get_position` 不按截止时间过滤；「死扛亏损」= `plan_stop` 已触发且下一交易日前未卖出。
- **「我的补充」的概念标签**：`agent.user_input` 放在 `UserInputs` 组件上方一行（标题在组件内部，本轮不改该组件）。

### 已知限制

- **概念卡不按时点回读**：卡随代码版本走，`get_concept` 与卡页读到的永远是当前版，不受截止时间约束。
- **不保存旧版正文**：`@n` 指向旧版时只返回当前版加 `stale` 说明；Agent 实际读到的正文连同 SHA-256 存在 `agent_tool_calls`，旧版看 git 历史。
- **改卡需重启服务**，没有热加载。
- **`get_concept` 是否被调用由模型决定**，不保证每次运行都会读卡。
- **给 Agent 的卡正文**会改写卡内的行内链接（围栏代码块与行内代码里的写法保持原样）；卡链接写在四空格缩进代码块里时，改写数与渲染出的链接数对不上，该卡校验失败（提示「概念卡链接必须是行内的……或写在缩进代码块里」），所以卡里不要这样写。
- **`plans` 的候选窗口**比图上日期范围更宽（用于列出有效期覆盖图上区间的全部计划）。

### 验收

- 测试：离线 pytest 1521 passed / 495 skipped；一次性库 pytest 2016 passed；前端 `npm test` 411 passed（34 个文件），`npm run build` 通过；`knowledge check` 通过（88 张卡、原理页 12 节）。
- **验收未完成**：只有自动化测试，没有真实浏览器用户验收，服务尚未重启；不宣称用户验收已过。重启服务、打 tag `v2.6.0`、推送 `origin` 不在本轮执行，各自先征得用户同意。

## v2.5.0 — 2026-10-28

计划时间线（[spec](docs/specs/2026-10-28-v2.5-plan-timeline-design.md)、[实施 plan](docs/plans/2026-10-28-v2.5-plan-timeline.md)）：Agent 计划结构化为可复查的论点并成为默认复查对象，同一单元的 Agent 计划组成版本链，批处理每日复查，系统信号与你贴入的信息触发修订，每版各自影子跟踪。状态：`engineering_done`、`user_acceptance_pending`（迁移 026 已于 2026-10-28 在真实库执行、服务已重启跑 v2.5 代码；没有真实浏览器验收与定时运行证据）。保留偏差与已知限制见下文与 [roadmap](docs/archive/v2/roadmap/roadmap.md)。

### 新增

- **三张新表（迁移 026）**：`agent_plan_versions`（版本链：`version_no`、`origin` = `signal` / `review` / `recheck`、`supersedes_plan_id`、`cutoff_at`、宿主差异 `diff`、模型变化说明 `changes`）、`plan_reviews`（每条复查结论：`maintain` / `revise` / `invalidate` / `skipped` / `error`、`decided_by`、信号、条件判定、引用的用户输入）、`user_inputs`（只追加的用户输入，更正与撤回都是新行）；`signal_decisions` 新增 `agent_plan_id`、`review_view` 两列；放宽 `agent_runs.mode`（`review`、`revise`）、`agent_tool_calls.phase`、`stage_runs.stage` 的约束。三张表都不进 `WORKBENCH_TABLES`。**026 待用户授权在真实库执行，必须先迁移、后重启服务**。
- **条件集合 `condition-1`**（`agent/conditions.py`）：11 种失效 / 复查条件（`close_below`、`close_above`、`no_entry_by`、`no_entry_before_earnings`、`earnings_within`、`regime_is`、`sector_weak`、`rs_below`、`new_filing`、`date_reached`、`next_session`），参数校验（价格在最近收盘 ±50%、日期在生成交易日后 60 天内）与三态判定（成立 / 未成立 / 无法判定，缺数据一律「无法判定」）。
- **Agent 计划 `agent-2`**：提议增加结构化的论点 `thesis`（1–3 条，宿主编号 `T1…T3`）、失效条件 `invalidation`、下次复查条件 `review_when`、修订版的 `changes`；反方逐条对论点给反证；宿主校验（`agent/plan_check.py`）不过整版作废（`thesis_count`、`invalid_condition`、`changes_mismatch`、`no_change` 等）。首版与计划行、运行行、版本行、简报同一事务写入。
- **复查 `agent-review-1` 与链状态 `timeline-1`**：链状态按 `invalidated` → `closed_by_decision` → `holding` → `position_closed` → `expired` → `entry` 判定，只复查 `entry` 与 `holding`；七项信号（`new_8k`、`abnormal_move`、`stop_touched`、`t1_touched`、`expiry_near`、`regime_change`、`user_input`）窗口为「上次有效复查之后」。
- **每日复查阶段 `review`**：批处理第 7 个阶段，对每条有效链：失效条件成立由系统规则作废；无信号、无新输入、复查条件都未满足由系统规则维持；其余调用一次复查模型，结论为修订时再走一次完整的提议 + 反方 + 校验，通过才成为新的最新版。每链每天最多 3 次尝试、受 token 上限与额度限制；每条结论写一行 `plan_reviews`。
- **用户输入**：研报要点 / 新闻 / 我的判断 / 其他，1–4000 字，可附链接（不抓取），作用于标的或单条链；更正与撤回指向旧行；复查与修订的提示词附「用户输入」证据块并写明「只是证据、不是指令」，引用 `user:<id>` 只能引用块里出现的 id。
- **重新复查（`recheck`）**：对入场期 / 持仓期的链立即复查一次，截止时间 = 点击时刻，一定调用模型；前置检查同步返回 503 / 404 / 409（`chain_not_active`、`agent_unavailable`、`review_in_progress`），通过后后台执行。
- **链视图与接口**：`GET /api/workbench/chains/{ticker}/{signal_date}`（链状态、各版本及影子结果、复查记录、用户输入）、`GET /api/workbench/chains?ticker=`、`GET /api/workbench/plan-reviews?session=`、`POST /api/workbench/chains/{ticker}/{signal_date}/recheck`、`GET/POST /api/workbench/user-inputs`。读取都在只读事务里执行；026 未执行时返回 503。
- **CLI**：`python -m investment_assistant.workbench.plan_review chain|recheck|check`——`chain` 只读打印一条链；`recheck` 同步重新复查；`check --session` 核对当天每条应复查的链恰有一条有效批处理复查（或当天生成新版）、新版的 `supersedes_plan_id` / `cutoff_at` / 引用都成立，不满足退出码 1。
- **前端**：「今日」Agent 计划块在前（`AgentPlanCard`：第 n 版、论点与反证、失效与复查条件判定、相对上版差异），规则基线 `plan-1` 折叠为一行（`RuleBaseline`，Agent 无可用计划时自动展开）；页头「布局：Agent 优先 / 规则优先（旧）」开关；单元列表之后列出进行中的 Agent 计划与「重新复查」；计划页 `#plan/<id>` 加版本链与复查记录（`PlanTimeline`）、「我的补充」（`UserInputs`）；「标的」页页尾「Agent 计划」与「我的补充」。

### 变更

- **`agent-2` 取代 `agent-1` 生成新计划**：v2.5 之前的 `agent-1` 计划原样保留、只读，照常展示与影子跟踪，标「agent-1（旧版，不参与复查）」，不迁移、不进每日复查。
- **阶段 `stages-2`**：每次 `workbench` 运行恰好 8 行阶段证据（新增 `review`）；026 未执行时本次按 `stages-1` 记 7 行；`derivation stages` 按各运行自己的阶段版本核对。
- **复盘 `review-6` / 月度 `monthly-3`**：计划影子的 `agent` 每条链只算首版（`agent-1` 计划算自己），新增 `agent_latest`（每条链的最新版），避免样本被修订次数放大；旧快照不重算。
- **推送**：新计划消息里有 Agent 结果的单元 Agent 行在前（`agent-2` 写「Agent 第 n 版：…」、链作废 / 无可用计划写原因），规则计划作为「规则基线」在后；概览增加「计划更新 修订 a · 作废 b」；有作废或产生新版的修订时新增一条「🔁 计划更新」消息（平常五条，有更新时六条）。
- **决定记录**：`POST /api/workbench/decisions` 可带 `agent_plan_id`、`review_view`；单元有链时 `agent_plan_id` 不填由后端填为最新版，填了旧版返回 409 `agent_plan_stale`。
- **「今日」Agent 视图基于链的最新版**：`agent-2` 单元的 `agent` 取链头的运行与计划，并带 `chain`（版本号、截止时间、差异、状态、最近复查）；另有 `chains`、持仓行的 `has_chain`。
- **三方对比**：行上新增 `version_no` 与 `version_basis`（决定时看到的版本 / 首版），口径版本仍为 `tracks-1`，`agent-1` 单元其余字段不变。
- **计划影子按版本**：`plan-shadow-1` 对链上每一版各自模拟，`GET /api/workbench/plans/shadow` 每行带 `chain`，响应另有 `agent_summary: {first, latest}`。

### 实现偏差

- **新增运行原因 `plan_invalid`「修订版按规则作废」**：spec D23（入场期修订版按规则作废）需要一个运行原因，原因表里没有。
- **`max_attempts` 先于其余预算闸门判定**：spec §6.2「3 次之后不再写行」与 §7.4 第 3 步的顺序冲突，按前者实现。
- **复查全程持有会话级锁**：每条链的一次复查从读取、调用模型到写入，在单独连接上持有会话级 advisory lock `review-run:<标的>:<信号日>`，读写事务里另有事务级锁 `review:<标的>:<信号日>`；plan 的短锁设计给不出 spec D14 / §13 要求的互斥。
- **新增 `GET /api/workbench/chains?ticker=`**：「标的」页列出 Agent 计划（spec §11）需要它，§12 接口表里没有。
- **409 文案**：`agent_plan_stale: Agent 计划已更新到第 n 版，请刷新后再决定`（plan 写的是 `agent_plan_stale: <n>`），更接近 spec §9.3 / §11 的页面文案。
- **决定接口的 503 只在请求带了 `agent_plan_id` / `review_view` 且 026 未执行时返回**：旧的决定请求不受影响（spec §11 / §12「旧决定不受影响」）。
- **「计划更新」时间窗（D25）以上次推送读取复查记录的时刻为起点**：取 `summary.push.reviews_until`（旧记录退回 `created_at`），休市日跳过的推送不移动窗口。
- **其他细化**：`today()` 多返回 `timeline_ready`；026 未执行时手动重新运行 Agent 记 `skipped`（`migration_026_missing`）而非 503；`diff` 实现在 `agent/plan_check.py`，`timeline` 复用（spec §4.1 写作 `timeline.diff`）；信号记全部 7 项（spec §6.2 写「六项」，§7.3 表列 7 项）；修订不单设 `run_revision`，即 `run_unit_v2(previous=…)`；Task 11–12 的前端文档在单独的 docs 提交里落地。

### 已知限制

- **链内拆股不换算**：修订版价位不做拆股换算，影子与纪律以信号日为拆股基准；出现时在验收记录如实标注，留 backlog。
- **扩展池入场期的链只复查、不推送**：排在最后复查，结果只影子跟踪。
- **持仓不重新关联到新版**：持仓的 `plan_stop` / `plan_target` 照旧按决定关联的那份计划判断，新版只是建议；没有 Agent 链的持仓不新建链。
- **链标的离开观察池后只采集数据、不跑规则、不产生候选**。
- **手动「重新推送今日计划」不移动「计划更新」时间窗**。
- **当日缺日线时止损 / T1 信号窗口照样前移**（backlog）。
- **`today()` 每条链各做若干次查询**，链越多越慢（backlog）。

### 迁移与验收

- **026 已在真实库执行**（2026-10-28，用户授权 Task 14）：备份后 `psql --single-transaction` 执行，再重启服务（**先迁移后重启**）；记录见 [迁移记录](docs/operations/migrations-and-backup.md#026-的真实库执行逐步手动用户-2026-10-28-授权执行-v25-plan-task-14)，手动验收见 [v2.5 验收记录](docs/archive/v2/roadmap/v2.5-acceptance.md)。026 未执行时批处理照常（`stages-1`、Agent 提议记 `skipped / migration_026_missing`、不复查、不发「计划更新」），新接口返回 503。
- 测试（`41b4738`）：离线 pytest 1347 passed / 480 skipped；一次性库 SQL pytest 1827 passed；前端 `npm test` 322 passed（29 个文件），`npm run build` 通过。全量 code review（`53d9ed5` 以来）无 Critical，Important 1 条（过时前端测试）与 5 条 Minor 已在 `41b4738` 修复。
- **验收未完成**：本轮只有自动化测试，没有真实浏览器用户验收、没有迁移后的定时运行证据（spec §14.3 连续 10 个交易日等）；不宣称用户验收或定时验收已过。打 tag、推送不在本轮执行，各自先征得用户同意。

## v2.4.0 — 2026-10-27

计划推导链与批处理 workflow 底座（[spec](docs/specs/2026-10-27-v2.4-derivation-and-workflow-design.md)、[实施 plan](docs/plans/2026-10-27-v2.4-derivation-and-workflow.md)）。状态：`engineering_done`、`user_acceptance_pending`（迁移 025 已于 2026-10-27 在真实库执行、服务已重启跑 v2.4 代码；§12.2 手动验收已核对、用户已确认推导可读，§12.3 连续 5 个交易日定时验收待完成，见[验收记录](docs/archive/v2/roadmap/v2.4-acceptance.md)）。保留偏差与已知限制见下文与 [roadmap](docs/archive/v2/roadmap/roadmap.md)。

### 新增

- **阶段证据表 `stage_runs`（迁移 025）**：每次 `workbench` 任务按固定注册表（`stages-1`：`collect`、`screen`、`regime`、`signals`、`plan`、`agent`、`push`）每阶段追加一行证据——状态、输入与产出计数、按原因分组的跳过、逐目标失败、降级、产出引用、耗时；每次运行恰好 7 行，`run_id` 与该次运行报告相同。表只追加、不进 `WORKBENCH_TABLES`：缺表时批处理照常完成，只记 `summary.stage_status.error`；推送阶段存在时序嵌套（筛选嵌在采集窗口里），展示顺序以注册表为准。
- **计划推导链（`derivation-1`）**：`plan-1` 规则计划按代入值逐步重算（TR、ATR、入场区间、止损、T1、有效期、财报与环境提示、按风险定仓、状态），与已存值逐项比对；自定计划价位标「你的输入」、Agent 计划价位核对 `agent_runs` 引用，两者仓位按入场区间中点推导；触发段逐句复核候选冻结的取值（`holds` / `violated` / `not_frozen`）。推导不调用 `plans.py` 的计算函数，常量分开声明并由测试对齐，两份实现彼此对照。一致性结论 `consistent` / `mismatch` / `incomplete`。
- **三个只读接口**：`GET /api/workbench/plans/{plan_id}/derivation`（完整推导 + 一致性 + 运行证据）、`GET /api/workbench/units/{ticker}/{signal_date}/derivation`（无计划单元的触发段 + 计划 id 列表）、`GET /api/workbench/stage-runs?session=…|run_id=…`（阶段证据按运行分组，附 `STAGES` 定义与 `stage_runs_ready`）。读取全在只读事务里执行，缺行如实标 `incomplete`，不在读取时补拉。
- **只读 CLI**：`python -m investment_assistant.workbench.derivation check --since … [--until …] [--kind …]`（按日期区间逐份核对计划一致性，`status = ok` 的计划非 `consistent` 时退出码 1）与 `… derivation stages --session …`（核对每运行恰好 7 行、`run_id` 与 `job_reports` 对得上、时间窗内的运行都有阶段行）。
- **前端推导视图**：`PlanDerivation` 组件四段展示（触发条件表、输入快照、逐项计算、校验）与一致性徽标，证据状态说明常驻；「今日」规则 / Agent 计划块「展开推导」、无计划单元「触发依据」（点开后才请求）；独立页 `#plan/<id>`（不进侧栏，无 id 回落「今日」）。

### 变更

- **批处理记录带上运行报告的 `run_id`**：`stage_runs`、`data_fetch_runs`、`signal_candidates`、`screen_runs` 四张表的新行都带该次 `workbench` 运行的 `job_reports.run_id`（harness 生成）——关掉 v2.3 的「批处理记录 `run_id` 为空」偏差；历史行不回填。手动批处理有两个 `run_id`：API 返回、页面轮询用的 runner `run_id`，与 harness 写 `job_reports` 的 `run_id`；数据库记录一律用后者。
- **推导不一致进 `errors`**：批处理在 `plan` / `agent` 阶段之后只读回读并推导本次产出的计划，计数写进阶段 `detail.derivation`；出现 `mismatch` 或 `incomplete` 时阶段记 `degraded`、`summary['errors']` 追加 `derivation_mismatch: <ticker> <plan_id>`，运行报告状态随之为 `error`（缺陷应进运行报告；阶段证据写入失败则只记 `stage_status`，不进 `errors`）。校验只读，不改已存计划、不影响推送。
- **「复盘」计划链接改去推导页**：轨道对比里的「计划 xxxx」链接从 `#instrument/<ticker>` 改为 `#plan/<plan_id>`。

### 实现偏差与关联限制

- **两处实现细化（spec 分层不变，仅文件归置不同）**：推导与阶段证据的只读用例放在新模块 `workbench/derivation_view.py`（spec §9 的「`api/routes` → 用例与纯函数 → `repository`」分层不变，只是多一个文件）；`data_view._read_tx` 提升为 `service.read_only_tx()`，供「数据」菜单与推导视图共用只读事务，行为不变。
- **`push` 阶段 `input_count` 记实际发送数**：notifier 不暴露待发消息数，无法按 spec §4「待发消息数」记；已按已裁决口径记录。
- **触发段看不到具体 8-K**：spec §7.2 的条件表把 `filing_ids` 列为 `earnings_gap_up` / `earnings_gap_down` / `new_filing` 的复核取值，但候选触发段输出未带 `inputs.filing_ids`，用户无法从页面看到是哪份 8-K 触发——已知 spec 缺口，按已知限制记录。

### 迁移与验收

- **025 待授权执行**：只建 `stage_runs` 表、约束与索引（`IF NOT EXISTS` 可重跑），不改旧表、不加种子行；真实库备份、执行、服务重启均需单独授权，结果追加到 [迁移记录](docs/operations/migrations-and-backup.md)。迁移未执行时推导接口照常返回（`run.ready = false`），`stage-runs` 接口返回 `stage_runs_ready = false`。
- **验收未完成**：本轮只有自动化测试（离线与一次性 PostgreSQL 全量、前端 Vitest 与构建），没有真实浏览器用户验收，没有迁移后连续 5 个交易日的运行证据；不宣称用户验收或定时验收已过。打 tag、推送不在本轮执行，各自先征得用户同意。

## v2.3.0 — 2026-10-26

数据底座与「数据」菜单（[spec](docs/specs/2026-10-26-v2.3-data-foundation-design.md)、[实施 plan](docs/plans/2026-10-26-v2.3-data-foundation.md)）。状态：`engineering_done`、`user_acceptance_pending`（迁移 024 已于 2026-10-27 在真实库执行、服务已重启跑 v2.3 代码；§11.2 手动验收已核对、用户已确认浏览可用，§11.3 定时验收待完成，见[验收记录](docs/archive/v2/roadmap/v2.3-acceptance.md)）；全范围 final review 已完成并修复，保留偏差与后续验收条件见 [v2.3 验收记录](docs/archive/v2/roadmap/v2.3-acceptance.md#工程自查与验收边界)。

### 新增

- **拉取记录表 `data_fetch_runs`**：迁移 024 新建只追加的记录表与索引；六族清单（日线、SEC 申报、资讯、ATR、FRED 利差、纳指 100 范围）集中定义用途与覆盖口径。覆盖和最新时点从数据表实算；ATR 从日线现算、不单独记拉取行。
- **四个数据接口**：`GET /api/data/families`（六族总览）、`GET /api/data/families/{id}`（详情、最近 20 次记录、逐目标覆盖）、`POST /api/data/families/{id}/fetch`（后台手动拉取）、`GET /api/data/browse/{id}`（固定窗口原始值）。读取走只读事务，不在读取时补拉；手动拉取复用共用采集函数，可全族或单只（仅日线 / 资讯 / SEC），不跑规则、不生成计划或候选、不改池、不推送。同族采集用 PostgreSQL advisory lock 互斥。
- **「数据」菜单**：位于「复盘」之后，六张状态卡展示用途、覆盖、最新时点、拉取成败与任务中心链接；详情有拉取记录、逐目标覆盖与「仅看滞后」、六族原始值浏览。ATR 按钮为「拉取日线」，范围为「重新加载」；页面常驻 Phase 0 Gate HOLD 证据说明，数据完整不代表信号有效。
- **两条任务记录路径**：`workbench` 批处理在采集结束、区制之前按族追加记录，常规路径五族各一行，显式池跳过筛选时不写 `universe`；`filings` 任务的下载器 / 监控两种模式结束后追加 `sec_filings`，目标取该模式实际采集的清单（监控模式为 `sec_monitor.watchlist_path`，下载器模式为 `config.watchlist`），用 `detail.targets_source` 与批处理的池 + 持仓区分。记录写入失败只记 `data_status.error`，不改变原任务成败。

### 变更

- **旧地址行为**：`#data` 进入数据总览，`#data/<id>` 进入族详情；v1 遗留及未知 `#data/...`（如 `#data/signals`）现在回落到「数据」总览，不再回落到「今日」。

- **「数据」页展示整理（2026-10-27，验收后）**：用途等后端文案里的反引号按代码样式显示，不再原样露出；证据说明只在页面顶部显示一次，卡片与浏览区只保留 ATR 的补充句；SEC 卡不再重复覆盖口径里已有的「无申报 ≠ 缺数据」。只改前端展示，接口与口径不变。
- **卡片「最近拉取」以全族记录为准（2026-10-27，验收后）**：主行改为「最近全族拉取」，只取全族记录；比它更新的单只拉取另起一行「最近单只拉取」，下一次全族拉取后收起；「最近成功」只算全族记录。接口卡片新增 `last_ticker_run`，`last_run` 改为最新全族记录。这与 spec §8「取最新一行」不同，是用户的决定：单拉一只不存在的代码曾让日线卡整张显示 `failed`。

### 实现偏差与关联限制

- **批处理记录的 `run_id` 为空**：采集阶段写记录时未接到任务 harness 的 `run_id`，目前只能用 `job` + `started_at` 对应运行报告；`filings` 在任务返回后记账，能保留任务的 `run_id`。
- **单族手动拉取使用 runner 的 `run_id`**：记录关联 `tasks/runner` 的运行，而非 `job_reports.run_id`；它本就不写 `job_reports`，结果经「数据」页轮询就地展示，或用 `GET /api/runs/{run_id}`（进程内）查询——「工具 → 运行记录」不显示它。此关联方式是 spec §5.1 允许的另一种来源，不表示生成了任务报告。

### 迁移与验收

- **024 待授权执行**：只建新表与索引，不改旧表；`data_fetch_runs` 不加入 `WORKBENCH_TABLES`。迁移未执行时覆盖与原始值仍可读，最近拉取不可见，手动拉取返回 503；任务记账失败不阻断原流程。真实库尚未执行 024，备份、迁移、服务重启均需后续单独授权。
- **验收未完成**：本轮只有自动化测试与一次性测试库证据，没有真实浏览器用户验收，没有迁移后连续 5 个交易日运行证据；不宣称用户验收或定时验收已过。打 tag、推送不在本轮执行。

## v2.2.0 — 2026-10-25

纳指 100 每日筛选与任务中心自描述（[spec](docs/specs/2026-10-25-v2.2-screener-and-task-center-design.md)）。状态：`engineering_done`、`user_acceptance_pending`（迁移 023 已于 2026-10-26 在真实库执行、服务已重启跑 v2.2 代码；§11.2/§11.3 验收证据积累中）。

### 新增

- **每日筛选 `screen-1`**：每个交易日收盘后从纳指 100（`config/universes/nasdaq100.json`，手动维护）筛出波段候选——触发组（`trend_reclaim` / `volume_breakout` 当日触发）优先、形态组（趋势 + ATR 区间）按相对强弱补齐，每天 ≤ 20 只——自动进扩展池（`pool_tiers.source = screen`），用 `plan-1` 复核；有效计划的筛选单元进待决定列表、按主列表推送，页面与推送带「筛选」标记。成员上限 `min(40, 100 − 手动观察池数)`，保留期 10 个交易日，锁定（有效计划或持仓）不挤出。筛选写入 `screen_runs` / `screen_results`（重跑追加、读取取最新）；范围文件加载失败或 QQQ 缺当日数据时沿用成员、批处理继续。
- **筛选报告**：推送第 5 条（概览 / 今日触发 → 复核 / 形态候选前 10 只）；「今日」筛选卡片（形态候选可展开全部）；`GET /api/workbench/screen`、`POST /api/workbench/screen/replay`（1–20 个交易日只读回放，不写表不推送）、`python -m investment_assistant.workbench.screen replay`。
- **任务中心自描述与北京时间**：任务说明移到后端 `tasks/catalog.py`，随 `GET /api/jobs/scheduled` 的 `description` 下发（新增字段，前端删 `JOB_HELP`）；任务中心、运行记录、「设置 → 定时任务」的时间统一按 `Asia/Shanghai` 显示并标「北京」；`filings` 任务改为北京 09:30（周一至周五）。

### 变更

- **市场环境 `regime-2`**：广度样本改为纳指 100 范围 ∪ 手动观察池，脱离「样本不足」；阈值不变，旧快照保留 `regime-1`。
- **复盘 `review-5`**：分池三分类——`core` / `screen`（`source = screen` 的计划）/ `extended`；旧计划按原规则归类。
- **计划上下文 `context-2`**：`inputs.context` 增加 `source` 与 `screen`（`screen_run_id`、分组、RS、排名）。

### 迁移

- **023**：`screen_runs`、`screen_results`、`pool_tiers.source`（默认 `watchlist`，新增字段、不是不兼容变更）；`filings` 任务时区更新。真实库执行需单独授权（备份 + `psql --single-transaction`）。

## v2.1.0 — 2026-10-25

v2 路线的收尾版：收拢 v1 遗留功能（[spec](docs/specs/2026-10-25-v2.1-legacy-consolidation-design.md)）。状态：`engineering_done`、`user_acceptance_pending`；迁移 022 已于 2026-10-25 在真实库手动执行（见 [operations/migrations-and-backup.md](docs/operations/migrations-and-backup.md)）。

### 删除

- **Hermes 宏观分析与决策证据（DeepSeek）**：`hermes/`、`services/hermes.py`、`/api/hermes/*`、「交易」页；`run_log` 先移到共享底座 `tasks/run_log.py`。
- **SPY/VIX 市场信号**：`market/`、`market_signals` 表、任务 `metrics` 的信号部分、`/api/market/*`、「数据 → 信号总览」；决策单元不再带 `market_status` 字段。
- **个股趋势快照**：`tickers/`、`ticker_signal_snapshots` 表、`/api/tickers/*`、「技术面趋势」页。
- **策略评分**：`strategies/`、`strategy_scores` 表、任务 `scores`、`/api/strategies/*`、「策略」页。
- **事件研究与研究观察卡**：`research/`、`services/research.py`、任务 `event_study`、`/api/research/*`（含 `us-references`）、`event_studies` 与 `research_observation_cards*` 表；关注列表「下一次事件」改读财报日。
- **推送路由与 `daily` 频道**：`NotifyConfig` 默认路由与已存设置只保留 `filings`、`workbench`、`workbench_review`；`DiscordChannel.DAILY`、`DISCORD_WEBHOOK_DAILY` 删除；已存路由失效时返回结构化错误，不抛异常。

### 并入

- **「标的」页 K 线图**：MA20/50/200 与成交量（最多 250 根日线，MA 用满 450 根历史），图下注明「拆股调整、未分红调整」。
- **「标的」页基本面一栏**：美股财季数据、来源展开、指引录入与手动刷新（原「数据 → 美股研究数据」）；读取失败只影响本栏。
- **上线前检查 `backcheck-2`**：信号后 5/10/20 个交易日相对 SPY 的超额统计（n、均值、中位数、正超额占比）；`backcheck-1` 旧记录超额列显示「—」。
- **SEC 文件记录**：移到「更多 → 工具 → SEC 文件」。

### 迁移

- **`022_drop_v1_legacy.sql`**：删除 `strategy_scores`、`ticker_signal_snapshots`、`market_signals`、`event_studies`、`research_observation_card_versions`、`research_observation_cards` 六张表与 `metrics`、`scores`、`event_study` 调度行；`job_reports` 历史与已冻结的 `backcheck-1` 记录保留；无 `CASCADE`。真实库手动 psql 执行（见 [operations/migrations-and-backup.md](docs/operations/migrations-and-backup.md)）。

### 不兼容变更

- API：删除 `/api/hermes/*`、`/api/market/*`、`/api/tickers/*`、`/api/strategies/*`、`/api/research/*`；`/api/us-research/*` 不变。
- 决策单元（`synthesize` 输出、`/api/workbench/*`）不再有 `market_status` 字段。
- 配置：`config.py` 删除 `market`、`strategy`、`backtest`、`llm` 段与 `model_default`（旧配置文件不改也能加载）；`DEEPSEEK_API_KEY`、`DISCORD_WEBHOOK_DAILY` 不再使用。
- 前端：`#data/...`、`#technicals/...`、`#strategy/...`、`#trade/...` 回落到「今日」。

## v2.0.0 — 2026-10-25

第一个正式版本。完成 roadmap v2 的「数据 → 交易计划 → 复盘」闭环，只做美股。各阶段都是 `engineering_done`，使用验收（`user_acceptance_pending`）仍在进行中。

### 新增

- **美股投研工作台**（[spec](docs/specs/2026-10-23-us-research-workbench-design.md)）：每日批处理采集日线、资讯、SEC 列表，运行规则 v1，按股票、按日合成决策单元；「今日」「标的」「复盘」页面；决定与成交只追加；对照组和周度复盘。
- **P1 规则计划与推送**（[spec](docs/specs/2026-10-23-p1-rule-plans-and-push-design.md)）：ATR、下次财报日；`plan-1` 计划（入场区间、止损、T1、有效期、按风险定仓、超限作废）；持仓止损与移动止损；计划影子跟踪；四个纪律指标；资金快照与出入金；月度收益与分档；北京时间 06:30 Discord 推送。
- **P2 观察池与市场环境**（[spec](docs/specs/2026-10-23-p2-pool-and-market-context-design.md)）：观察池上限 100 只，分核心池（≤ 15 只）与扩展池；行业 ETF 强弱；池内广度与区制标签；FRED HY 利差与 2s10s；上线前检查 `backcheck-1`。
- **P3 Agent 轨道**（[spec](docs/specs/2026-10-24-p3-agent-track-design.md)）：LangGraph + 智谱；工具调用写库并做引用校验；反方步骤；每日 token 预算与额度识别；Agent 计划进入影子跟踪，可选作执行依据。
- **P4 复盘扩展**（[spec](docs/specs/2026-10-25-p4-review-expansion-design.md)）：规则 / Agent / 实际三方对比；滚动 3 个月复查、敏感性表；周度与月度复盘推送（任务 `workbench_review`）；解读统一由 Agent 轨道产出，退役 Codex 解读。
- **文档重构**（[spec](docs/specs/2026-10-25-v2-release-cleanup-design.md)）：架构、功能（按业务能力拆分）、运维、roadmap、spec、plan 各自归位；`tests/test_docs_links.py` 检查相对链接。

### 不兼容变更

- **删除 A 股**：删除 `investment_assistant/ashare/`（含 CNEquity vendor）、`/api/ashares/*`、任务 `ashares`、「数据 → A 股研究数据」页、研究观察卡的 A 股 / 美股切换。
- **观察名单只认美股代码**：`POST /api/watchlist` 提交 A 股代码时返回 400；`GET /api/watchlist` 不再接受 `market` 参数（传了也忽略），响应里不再有 `board` 字段；`/api/watchlist/resolve` 只返回美股候选。
- **研究观察卡**：情景字段 `profit_cny` 删除，利润须同时写 `profit` 和 `currency`。
- **迁移 `021_drop_ashare.sql`**：删除 `cn_observations`、`cn_collection_state`，以及 CN 观察名单行、`CN:` 研究卡及其版本、`ashares` 任务行；`job_reports` 的历史行保留。
- **只支持本机部署**：删除 `deploy/`（NUC + systemd）、`Dockerfile`、`.dockerignore`、`scripts/build-push.sh` 和 Sealos 部署文档。
- **文档路径**：spec 和 plan 从 `docs/superpowers/` 移到 `docs/specs/`、`docs/plans/`；v1 时期的 spec 和 plan 移到 `docs/archive/roadmap-v1/`。

### 已知限制

- 所有规则都未经验证（Phase 0 正式 Gate HOLD）；复盘只给描述性统计。
- 已在真实库验收的部分有：迁移、服务重载、页面与接口检查、一次真实周度推送。尚未出现第一个无冲突的买入单元，所以真实数据上的计划价位、Agent 计划和三方对比还没有核对。待验收清单见 [roadmap §6.4](docs/archive/v2/roadmap/roadmap.md#64-未完成的使用验收)。
- v1 遗留功能（Hermes、市场信号与策略评分、事件研究、美股财季数据与研究观察卡）原样保留，下一版决定合并还是删除。
- yfinance 资讯源不稳定；Agent 额度错误码按文档初值实现，还需要用真实响应核对。
