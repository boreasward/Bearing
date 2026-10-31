# 文档索引

建议按这个顺序读：[architecture.md](architecture.md) 了解系统由哪些部分组成；[getting-started.md](getting-started.md) 在本机跑起来；再读 `features/` 了解每个功能。

## 系统现在是什么样

| 文档 | 内容 |
| --- | --- |
| [architecture.md](architecture.md) | 运行形态、代码分层、主线数据流、数据表分组、定时任务与前端 |
| [getting-started.md](getting-started.md) | 安装、配置（`.env` 与配置文件）、建库、启动、测试 |
| [design-system.md](design-system.md) | 设计标准：原则、令牌（浅 / 深取值）、布局、状态词汇与语气、数据块五态、组件清单与 API、交互、图标与图表、文案；组件画廊在 `npm run dev` → `#_ui` |

### 功能（features/）

每篇都写：做什么、页面与 API/CLI 入口、怎么验证一次、已知限制。

| 文档 | 功能 |
| --- | --- |
| [daily-plan.md](features/daily-plan.md) | 每日工作流（十个节点、检查点与续跑、运行详情页）、规则 v1、交易计划（`plan-2` 的执行字段）、「今日」计划表与成交关联计划（v2.9）、持仓止损、每日推送 |
| [agent-track.md](features/agent-track.md) | Agent 轨道：ChatGPT 订阅模型层与连接、工具层、提议与反方、引用校验、预算、解读；一只股票一条活跃链、再次触发、退出期与持仓状态（v2.9） |
| [review.md](features/review.md) | 影子跟踪、纪律、三方对比、周/月复盘（`review-8` / `monthly-4` 的执行口径）、资金与月度收益、复盘推送 |
| [pool-and-context.md](features/pool-and-context.md) | 观察池分层、每日筛选（纳指 100 `screen-1`、成员轮换、报告与只读回放）、市场环境（广度、区制、行业、FRED）、上线前检查 |
| [data-menu.md](features/data-menu.md) | 「数据」菜单：数据族覆盖与最新时点、拉取记录、单族手动拉取、原始值浏览 |
| [knowledge.md](features/knowledge.md) | 知识层：版本化的概念卡与原理页——卡的格式与校验、引用形式、加载与缓存、`/api/knowledge/*` 接口、`knowledge check` / `show` 命令 |
| [sec-filings.md](features/sec-filings.md) | SEC 下载器与 SEC 监控 |
| [fundamentals.md](features/fundamentals.md) | 「标的」页的基本面一栏：美股财季数据与公司指引 |
| [legacy/](features/legacy/README.md) | v1 遗留功能的墓碑页：逐项列出 v2.1.0 的删除与去向（旧实现见 tag `v2.0.0`） |

### 运维（operations/）

| 文档 | 内容 |
| --- | --- |
| [local-service.md](operations/local-service.md) | LaunchAgent、启动链路、重启、日志与路径、认证 |
| [scheduling-and-notifications.md](operations/scheduling-and-notifications.md) | 调度线程、任务注册表、运行报告、通知配置 |
| [migrations-and-backup.md](operations/migrations-and-backup.md) | 迁移清单 001–029、真实库备份与执行、一次性测试库 |
| [../scripts/README.md](../scripts/README.md) | 一次性核查脚本：`scripts/siwc_precheck.py`（v2.8 开工前 ChatGPT 核查，由你在运行服务的这台 Mac 上执行，结果追加到 v2.8 spec §17） |

## 方向与历史

| 位置 | 内容 |
| --- | --- |
| [roadmap/roadmap.md](roadmap/roadmap.md) | 唯一的路线文档：最终目标与成功标准、原则与边界、版本总览、当前路线 v2.5–v2.13（v2.8 于 2026-10-28 重定义为每日工作流，2026-10-29 发布 `v2.8.0`；v2.9 可执行的计划于 2026-10-30 插入并发布 `v2.9.0`——[spec](specs/2026-10-30-v2.9-executable-plans-design.md)、[plan](plans/2026-10-30-v2.9-executable-plans.md)；v2.10 设计标准与界面重构于同日插入，2026-10-31 发布 `v2.10.0`、同日修正版 `v2.10.1`——[spec](specs/2026-10-30-v2.10-design-system-and-ui-refactor-design.md)、[plan](plans/2026-10-30-v2.10-design-system-and-ui-refactor.md)；v2.11 账户与模拟交易于 2026-10-31 插入，实施已开工——[spec](specs/2026-10-31-v2.11-accounts-and-paper-trading-design.md)、[plan](plans/2026-10-31-v2.11-accounts-and-paper-trading.md)）、远期方向、backlog、DoD 与状态用语、决策记录；同目录 `v2.x-acceptance.md` 是各版验收证据 |
| [roadmap/v2.8-runtime-feasibility.md](roadmap/v2.8-runtime-feasibility.md) | 本地 CLI 运行时可行性报告（2026-10-28）：`claude-code`、`codex`、`pi` 在无人值守、禁用自带工具、接本地 MCP、token 用量、批处理时限五项上的实测。**同日被 roadmap 决策 #63 取代**（v2.8 不再做运行时切换），保留为历史依据 |
| [specs/](specs/) | 各阶段的设计 spec |
| [plans/](plans/) | 各阶段的实施 plan |
| [archive/roadmap-v1/](archive/roadmap-v1/README.md) | roadmap v1 及其 spec、plan、评审 |
| [../CHANGELOG.md](../CHANGELOG.md) | 按版本记录的变更 |

## 维护规则

| 类别 | 回答的问题 | 什么时候改 |
| --- | --- | --- |
| roadmap | 往哪走、为什么、走到哪一步了 | 方向变化时改；进度文档按周更新；版本完成后冻结 |
| spec | 某个阶段为什么这样设计 | 写成后冻结，只增不改；命名 `specs/YYYY-MM-DD-<topic>-design.md` |
| plan | 某个阶段怎么实施 | 写成后冻结，只增不改；命名 `plans/YYYY-MM-DD-<topic>.md` |
| features / architecture / operations | 系统现在能做什么、怎么用、怎么验证 | 和代码在同一个提交里更新 |
| 设计标准（design-system） | 前端用什么令牌、组件、状态词汇和交互规则 | 组件、令牌或交互规则变了，与代码同一个提交更新 `design-system.md` |
| knowledge（概念卡与原理） | 系统里每个概念是什么意思、系统怎么跑 | 与改口径的代码同一个提交更新；口径变了该卡 `version` 加 1（改措辞、补例子不加）；改卡后需重启服务生效 |

冻结文档只修失效链接，不改正文。`tests/test_docs_links.py` 会检查所有相对链接都指向存在的文件。
