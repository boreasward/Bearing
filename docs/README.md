# 文档索引

建议按这个顺序读：[roadmap.md](roadmap.md) 看 v3 走到哪一步；v3 的设计见 [specs/](specs/)、实施 plan 见 [plans/](plans/)；旧系统（v2.15）怎么跑见 [getting-started.md](getting-started.md)。

## 系统现在是什么样

v3 重做进行中；旧系统（v2.15）在切换到 v3 前照常运行。

| 文档 | 内容 |
| --- | --- |
| [roadmap.md](roadmap.md) | v3 路线：一句目标、spec §15.6 第 0–5 步与状态；直接修改维护 |
| [specs/2026-11-05-agent-first-rebuild-design.md](specs/2026-11-05-agent-first-rebuild-design.md) | v3 设计：agent 入口与每日简报、规则草稿、MCP 工具面、页面服务与对话、保留与删除 |
| [plans/2026-11-05-v3-facts-and-tools.md](plans/2026-11-05-v3-facts-and-tools.md) | v3 第一阶段实施 plan：文档归档、Pi 无人值守可行性验证、事实层与工具面 |
| [operations/](operations/) | 运维文档：本地服务、调度与通知、迁移与备份；v3 新增的运维说明也写在这里 |
| [design-system.md](design-system.md) | 设计标准：原则、令牌（浅 / 深取值）、布局、状态词汇与语气、数据块五态、组件清单与 API、交互、图标与图表、文案；组件画廊在 `npm run dev` → `#_ui`（v3 保留的组件层仍以它为标准，spec §15.1） |
| [getting-started.md](getting-started.md) | 旧系统（v2）的上手指南：安装、配置（`.env` 与配置文件）、建库、启动、测试；切换前照用 |

### v2（已归档）

v2 的路线、功能与架构文档已移入 [archive/v2/](archive/v2/)，只读：

| 位置 | 内容 |
| --- | --- |
| [architecture.md](archive/v2/architecture.md) | v2 的运行形态、代码分层、主线数据流、数据表分组、定时任务与前端 |
| [features/](archive/v2/features/) | v2 的各功能文档：每日工作流、Agent 轨道、账本与模拟交易、复盘、观察池与市场环境、数据菜单、数据注册表、研究、组合经理、系统内 roadmap、功能开关、知识层、SEC 下载与监控、基本面、v1 墓碑页 |
| [roadmap/](archive/v2/roadmap/roadmap.md) | v2 的路线与决策 #1–#124；同目录 `v2.x-acceptance.md` 保存各版真实验收证据 |

### 运维（operations/）

| 文档 | 内容 |
| --- | --- |
| [local-service.md](operations/local-service.md) | LaunchAgent、启动链路、重启、日志与路径、认证 |
| [scheduling-and-notifications.md](operations/scheduling-and-notifications.md) | 调度线程、任务注册表、运行报告、通知配置 |
| [migrations-and-backup.md](operations/migrations-and-backup.md) | 迁移清单 001–031、真实库备份与执行、一次性测试库 |
| [pi-unattended.md](operations/pi-unattended.md) | Pi 无人值守运行可行性（v3 第 0 步）：专用配置目录、实测命令行、事件与成败判定、十项检查结果 |
| [v3-facts.md](operations/v3-facts.md) | v3 事实任务与工具面（v3 第 1 步）：命令与退出码、草稿位置、`v3` 配置键、并行期共用事实表、迁移 032 与真实库试跑记录 |
| [../scripts/README.md](../scripts/README.md) | 一次性核查脚本：`scripts/siwc_precheck.py`（v2.8 开工前 ChatGPT 核查，由你在运行服务的这台 Mac 上执行，结果追加到 v2.8 spec §17） |

## 方向与历史

| 位置 | 内容 |
| --- | --- |
| [roadmap.md](roadmap.md) | v3 路线（直接修改维护）：目标、spec §15.6 第 0–5 步与状态 |
| [specs/](specs/) | 各阶段的设计 spec（v3：[2026-11-05 agent 优先重做](specs/2026-11-05-agent-first-rebuild-design.md)） |
| [plans/](plans/) | 各阶段的实施 plan（v3 第一阶段：[2026-11-05 facts and tools](plans/2026-11-05-v3-facts-and-tools.md)） |
| [archive/v2/](archive/v2/) | **v2（已归档）**：路线与验收记录（[roadmap/](archive/v2/roadmap/roadmap.md)）、功能文档（[features/](archive/v2/features/)）、[architecture.md](archive/v2/architecture.md) |
| [archive/v2/roadmap/v2.8-runtime-feasibility.md](archive/v2/roadmap/v2.8-runtime-feasibility.md) | 本地 CLI 运行时可行性报告（2026-10-28）：`claude-code`、`codex`、`pi` 在无人值守、禁用自带工具、接本地 MCP、token 用量、批处理时限五项上的实测。同日被 v2 roadmap 决策 #63 取代（v2.8 不再做运行时切换），保留为历史依据 |
| [archive/roadmap-v1/](archive/roadmap-v1/README.md) | roadmap v1 及其 spec、plan、评审 |
| [../CHANGELOG.md](../CHANGELOG.md) | 按版本记录的变更 |

## 维护规则

| 类别 | 回答的问题 | 什么时候改 |
| --- | --- | --- |
| roadmap | 往哪走、为什么、走到哪一步了 | 仓库文件 `docs/roadmap.md`，直接修改 |
| spec | 某个阶段为什么这样设计 | 写成后冻结，只增不改；命名 `specs/YYYY-MM-DD-<topic>-design.md` |
| plan | 某个阶段怎么实施 | 写成后冻结，只增不改；命名 `plans/YYYY-MM-DD-<topic>.md` |
| operations | 系统怎么跑、怎么备份与恢复 | 和代码在同一个提交里更新 |
| 设计标准（design-system） | 前端用什么令牌、组件、状态词汇和交互规则 | 组件、令牌或交互规则变了，与代码同一个提交更新 `design-system.md` |
| knowledge（概念卡与原理） | 系统里每个概念是什么意思、系统怎么跑 | 与改口径的代码同一个提交更新；口径变了该卡 `version` 加 1（改措辞、补例子不加）；改卡后需重启服务生效 |

冻结文档只修失效链接，不改正文。`tests/test_docs_links.py` 会检查所有相对链接都指向存在的文件。
