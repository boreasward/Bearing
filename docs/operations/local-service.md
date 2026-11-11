# 本机服务

本机服务是 v3 保留下来的外壳：托管前端构建产物，并承载 v3.1 的页面接口（`/api/...`，见下）。没有调度线程：每日的采集与名单由命令行的 `facts run` 完成（见 [v3-facts.md](v3-facts.md)），定时简报由独立的 LaunchAgent 跑（见 [daily-run.md](daily-run.md)）。v2 的调度、任务、通知、Agent 连接都随旧系统移除（见 [roadmap](../roadmap.md)；原来的运维说明在 [archive/v2/operations/](../archive/v2/operations/scheduling-and-notifications.md)）。

## 当前状态

**服务在运行（v3.2.2 已上线；v3.3 代码与测试已落地、待上线，迁移 037 与重载均未执行，见下文 [v3.3 上线五步](#v33-上线五步未执行)）。** 2026-11-06 移除 v2 之前曾 `launchctl bootout` 停掉旧服务；2026-11-07 v3.1 上线时经用户授权重新 `bootstrap`，页面与 `/api/...` 接口在 `http://127.0.0.1:8787/` 对外。2026-11-09 v3.2 上线：迁移 035 执行后 `npm run build` 重建 `web/dist`、`launchctl kickstart -k` 重启服务；冒烟时 `/api/research`（两组）、`/api/research/{id}`（不存在 404 `unknown_research`）、`/api/retrospect`（空库 `empty` 为真、周界 2026-11-02–11-06）与非法参数的 400 都符合契约，首页「研究」「复盘」两块渲染、控制台无错误。同日 v3.2.1 上线：迁移 036 执行后重建 `web/dist` 并 `kickstart -k` 重启，首页换成三栏工作台；执行记录见下文 [v3.2.1 上线四步](#v321-上线四步2026-11-09-已执行)。2026-11-10 v3.2.2 上线：重建 `web/dist` 并 `kickstart -k` 重启，「今日」「账户」换成卡片、抽屉加「工具」面板；执行记录见下文 [v3.2.2 上线两步](#v322-上线两步2026-11-10-已执行)。
每日任务另有一个 LaunchAgent `local.investment-assistant.daily`（同日安装，见 [daily-run.md](daily-run.md)）；两者的 `INVESTMENT_ASSISTANT_RUNTIME_BASE` 都是 `~/Library/Application Support/investment-assistant`，共用其下的 `pi.lock`。

## 启动链路

```
~/Library/LaunchAgents/local.investment-assistant.plist（不在仓库里）
  Label = local.investment-assistant，RunAtLoad + KeepAlive，WorkingDirectory = 仓库根目录
  └─ ./start.command
       └─ .venv/bin/python scripts/start_local.py
            读取仓库 .env（缺文件就退出）；强制监听 127.0.0.1
            └─ investment_assistant.api.server（uvicorn，端口 HERMES_DASHBOARD_PORT，默认 8787）
```

服务处理这些请求：

- **页面接口（v3.1，`investment_assistant/api/routes/`）**，全部 JSON（金额与价位为字符串，日期 `YYYY-MM-DD`），错误一律 `{"error": "<code>[: 说明]"}`，校验错误 400，没有运行记录 404 `no_run`，运行时忙 409：
  - `GET /api/today`：今日操作与账户条；v3.2.1 增加 `counts: { actions, holdings, pending, watching }`，分别统计操作、持仓计划、待入场计划与观察中研究；空库四项为 0。v3.3：`account.exposure = { pct, cap, room, incomplete }`（仓位占比、上限、剩余额度均为字符串，没有资金记录时 `pct` 与 `room` 为 null；`incomplete` 为真表示有持仓缺最新收盘、占比偏低）；`actions[]` 每行加 `sell_quantity`（只在 `kind = rotation_sell` 时有值）、`counterpart`（换仓的 B）与 `stale_quantity`（止损单数量待改），`kind` 多一种 `rotation_sell`，排在 `sell_next_open` 之后、`unchecked` 之前。
  - `GET /api/market`（v3.2.1）：最新成功运行的市场快照（含 `session`、`calc_version`）与 `watch[]`。关注表按持仓计划 → 待入场计划 → 观察中研究对股票去重，带计划 / 研究 id；两根最近日线给出收盘价与两位小数百分比字符串，缺一根时三个价格 / 涨跌字段均为 null；没有成功运行返回 404 `no_run`。
  - `GET /api/runs?limit=20`（v3.2.1）：按交易日倒序的事实运行与对应简报（含未完成、推送、周报状态），以及配置的 `schedule`；`next_at` 是北京时间、ISO 带 `+08:00`。空库仍返回完整计划表与空 `runs`。`limit` 为 1–100，否则 400 `invalid_argument`。
  - `GET /api/briefs`、`GET /api/briefs/latest`、`GET /api/briefs/{session}`：简报列表与单份简报；两个单份简报接口除原字段外还返回 `sections`（按正文顺序的小节，含 `key` / `title` / `markdown`）、`lead`（错误简报提示）、`footer`（页脚）与 `market`（该简报对应运行的市场快照，不含 `watch`，取不到时为 `null`）。
  - `GET /api/plans`、`GET /api/plans/{id}`：计划三组卡片与单份计划（含变化记录）；`POST /api/plans/{id}/stop-order`、`/raise-stop`、`/cancel`：计划写入口。v3.3：卡片加 `rotation`（这份计划是 A 且换仓指令还在，`{ rotation_id, sell_quantity, counterpart }`，否则 null；只对 `open` 的计划）、`funded_by`（这份计划是 B，`{ rotation_id, holding_ticker, sell_quantity }`，否则 null）、`stop_quantity_stale`（布尔）与 `warnings`（保存时的警告列表）；`next_action.kind` 多一种 `rotation_sell`；新增 `POST /api/plans/{id}/rotation/withdraw`（body 带 `request_id`、`confirmed`，可选 `reason`）：撤回换仓指令，先复述、`confirmed` 为真才写，与对话工具 `withdraw_rotation` 共用同一个函数与校验，没有未执行完的换仓指令时 400 `no_rotation_instruction`。
  - `GET /api/research`（v3.2）：研究两组卡片（`group=watching|ended`、`limit`，观察中缺省）；`GET /api/research/{id}`：单份含变化记录时间线；`POST /api/research/{id}/drop`：放弃研究（先复述，`confirmed` 为真才写）；`GET /api/retrospect`（v3.2）：复盘——执行复盘记录（`limit` 阶梯 20 / 50 / 100）、周报（`week` 切换以前的周）与规则检验两张表；v3.3 加 `rotations = { records, summary, min_sample }`：`records` 每次比较一行（`rotation_id`、`session`、`origin`、`candidate`、`verdict` / `verdict_text`、`holding_ticker`、`reasons`、`weakness`、`sell_quantity`、`buy_shares`、`fee_estimate`、`execution`、`compare`；`compare` 是第 5 / 10 / 20 个交易日 A 与 B 的涨跌幅与差，日线不够写 `unfinished`），`summary` 是三种结论次数、执行了几次、手续费估计合计与 B 跑赢 A 的占比 / 差值平均（样本低于 `min_sample` 时占比与平均为 null），全部由宿主现算。
  - `POST /api/trades`、`/api/cash-flows`、`/api/reconcile`、`/api/voids`、`GET /api/account`：账本（写入口先复述，`confirmed` 为真才写）；v3.3：`GET /api/account` 加 `exposure`（同上），`POST /api/trades` 的返回加 `over_cap`（买入后仓位占比高于上限）与 `stop_order_quantity`（部分卖出后止损单需改成的股数）。
  - `GET /api/ledger?limit=20`（v3.2.1）：成交、出入金、对账、作废记录四张表各最近 `limit` 行，金额与价格为字符串；成交与出入金带 `voided_by` 和 `void_reason`。空库返回四个空数组；`limit` 为 1–100，否则 400 `invalid_argument`。
  - `GET /api/chats`、`POST /api/chats`、`GET /api/chats/{id}`：对话列表、新建、详情。v3.2.1 列表增加 `last_message_at` 并按它倒序（无消息时等于创建时间），消息增加 `agent_session`；`POST /api/chats/{id}/messages`：一轮提问，响应是 `text/event-stream`（SSE），每个事件一帧 `event: <kind>\ndata: <json>\n\n`。首帧 `event: start` 的数据为 `{"kind":"start","agent_session":"chat-…"}`，其余帧保留原字段，末帧必为 `done`/`error`/`cancelled`；`POST /api/chats/{id}/cancel`：取消当轮。对话轮次与每日简报共用一个运行时锁，简报运行期间提问得到 409 `brief_running`。
  - `GET /api/chats/{id}/calls?agent_session=chat-…&ref=call:N`（v3.2.1）：读取保存的 `{ ref, tool, args, result, error, called_at, truncated, result_available }`；会话必须以该对话的 `chat-{id}-` 开头，非法编号、其他对话或缺行均返回 404 `unknown_call`。`result_available` 是 `result IS NOT NULL` 的派生布尔值：历史 SQL NULL 为 false，成功 JSON null 为 true（JSON 中两者的 result 都是 null）；不新增数据库列。截断结果保持存储的占位对象和标记，工具错误来自保存行的 `error`。
  - `GET /api/settings`、`GET /api/settings/options`、`PUT /api/settings`：运行时与模型设置。
- `/` 返回 `web/dist/index.html`（页面构建产物）；`/assets/…` 返回构建产物里的文件。
- 其余路径，以及未登记的方法，一律 404 `{"error": "not found"}`。
- 这两类兜底（`GET` 的静态托管与其他方法的 404）是 `create_app()` 里最后注册的两个路由；新的 `/api/...` 路由必须在它们之前 `include_router`，否则会被拦成 404（有测试守着这个顺序）。
- 没有 `web/dist` 时 `/` 返回 503 `{"error": "frontend_not_built", …}`。
- 组件画廊（`#_ui`）只在开发模式（`cd web && npm run dev`）可用，构建产物不含它。

## v3.3 上线五步（未执行）

依据 [设计 spec](../specs/2026-11-11-v3.3-account-cap-and-rotation-design.md) §14。**以下五步每一步都需用户单独授权；截至本文写成时一步都没有执行**：真实库没有跑迁移 037，页面服务没有重载，每日任务的 LaunchAgent 没有动。v3.2.0 仍在观察期（roadmap 第 6 步），本版的迁移编号 037 排在 036 之后，不改动观察安排。

1. **备份真实库**（需用户单独授权，未执行）：`pg_dump -Fc` 全库备份到 `.local/v3/backup-before-037.dump`，记大小与 sha256，`pg_restore --list` 核对；命令见 [migrations-and-backup.md](migrations-and-backup.md#037-与-v33-上线五步未执行)。
2. **在真实库执行迁移 037**（需用户单独授权，未执行）：走 `migrate` 入口，预期输出恰为 `{"applied": ["037_v3_3_rotation.sql"], "count": 1}`；只新建一张表、给 `v3_plans` 加四列并放宽两处约束，不改旧行。执行前先确认没有进行中的 daily / facts / 对话轮次。新代码读新列，所以必须先于第 3 步。
3. **重建前端并重新加载页面服务**（需用户单独授权，未执行）：每日任务随下一次定时运行生效，不需要另装 LaunchAgent。

   ```bash
   git rev-parse HEAD
   cd web && npm run build
   cd ..
   test -s web/dist/index.html
   launchctl kickstart -k gui/$(id -u)/local.investment-assistant
   launchctl print gui/$(id -u)/local.investment-assistant | grep -E 'state|pid'
   curl --fail --silent --show-error http://127.0.0.1:8787/api/account
   ```

   重载后 `/api/account` 与 `/api/today` 的 `account.exposure` 应有值；页面的「工具」面板应列出 38 个工具。
4. **核对账户页的仓位占比与券商一致**（需用户单独授权，未执行）：此时如果仓位已高于 60%，系统只显示，不要求卖出，也不产生任何卖出指令（spec §5.3）；`total_cap` 默认值改为 0.6 随第 3 步生效（本机 `config/local.json` 没有设这个键）。
5. **观察并记发布**（需用户单独授权，未执行）：观察到第一次真实的换仓比较，或连续 5 个定时交易日没有触发而简报正常之后，把执行记录补到本文与迁移文档、更新 roadmap 第 7 行与 CHANGELOG 的 3.3.0 发布日期；打 tag 与 push 仍需单独授权。

**执行记录**：尚无。每步执行后补在本节（备份大小与 sha256、`applied` 输出、构建 HEAD、服务 pid、接口与页面核对结论）。

## v3.2.2 上线两步（2026-11-10 已执行）

依据 [设计 spec](../specs/2026-11-10-v3.2.2-page-cards-and-tool-list-design.md) §10，以下两步在 2026-11-10 经用户授权（「验收并完成发布」）依次执行。本版没有数据库迁移。

1. **重建 `web/dist` 并重载页面服务**：后端简报接口新增 `sections`、`lead`、`footer`、`market` 字段，部署需同时更新后端代码与前端构建产物，再重载本机页面服务。

   ```bash
   git rev-parse HEAD
   cd web && npm run build
   cd ..
   test -s web/dist/index.html
   launchctl kickstart -k gui/$(id -u)/local.investment-assistant
   launchctl print gui/$(id -u)/local.investment-assistant | grep -E 'state|pid'
   curl --fail --silent --show-error http://127.0.0.1:8787/api/briefs/latest
   ```

2. **真实页面核对**：在真实页面上按[设计 spec §12](../specs/2026-11-10-v3.2.2-page-cards-and-tool-list-design.md#12-验收)核对九项验收，包括今日与账户卡片、抽屉工具清单、深浅主题与响应式布局。

**执行记录（2026-11-10，本机服务，未启用 Basic）：**

- 上线前三套全量（HEAD `483d72a`）：离线 919 passed / 416 skipped，一次性库 1335 passed，前端 71 个文件 500 passed（`--maxWorkers=2`），svelte-check 951 个文件 0 错误 0 警告。
- 第 1 步：构建 HEAD `483d72a`，`web/dist/index.html` 非空；`launchctl kickstart -k` 后服务 pid 29603 → 52065，`state = running`，首页 200。`/api/briefs/latest`（2026-11-09，final）返回十个小节（`actions` … `unresolved`）、`lead` 为 null、`footer` 以「数据截至 2026-11-09 收盘」开头、`market` 为当天快照（`regime-2`、neutral，无 `watch`）；`/api/briefs/2026-11-06` 返回八个小节与 2026-11-06 的快照。
- 第 2 步核对中发现：页内改 hash（前进 / 后退、`#today` → `#today?session=2026-11-06`）时历史简报一直停在骨架、不发请求（新开页面或用日期下拉切换正常）。修复为 `e235623`（`path` 改为可写 derived，补 `TodayView.test.ts` 用例），定向 20 passed、前端全量 501 passed、svelte-check 0 错误；只重建 `web/dist`（静态资源每次从磁盘读，后端未变，未再重启）。
- 第 2 步页面（修复后）：1600px 宽两栏，右栏 320px，左栏「今日操作」「一句话」「和昨天比变了什么」「持仓」「待入场计划」「研究」「规则名单」「今天下不了结论的」各一张卡，「市场环境」「账户」小节不重复；页内切到 2026-11-06：行情卡交易日 2026-11-06、账户卡标「最新」、今日操作无按钮，后退回最新正常。「账户」页五张卡，「记成交」「记出入金」「对账」在各自标题行（未提交写入）。「工具」面板五组 35 个、写入标记齐全、行不可点、`ws.drawer = tools`（核对后切回「仪表」）。700px 账户与行情并排、抽屉为右侧 Sheet，420px 单列，均无横向滚动；深色主题卡片边框 `rgb(42, 42, 42)`、12px 圆角、无阴影。对话 `f341edc7…` 的 MarketCard 仍是「市场温度」与 `get_market · call:1` 来源行。控制台无错误。
- 真实库没有草稿或出错的简报，§12 第 4 项（「二次确认未完成」、出错简报只显示一条原因、没有简报时照常打开）由 `TodayView.test.ts` 覆盖，未在真实页面上看到。
- 上线时的外观问题：右栏 320px 时行情卡 ETF 表的百分比折行（如 `12.77%`、`-12.98%`）。原因是 `MetricValue` 的 `break-all` 让表格把三列数字压到约 43px 内容宽，不折行需约 340px 的表格宽度；把右栏放到 spec 上限 340px 时卡片内容宽只有 314px，仍然折行。
- 2026-11-11 经用户同意收紧表格修复：`MarketCard` 的 `brief` 形态给 ETF 表加紧凑样式（12px 字、单元格左右 4px、首尾列贴边、百分比不折行），右栏宽度仍是 320px。只重建 `web/dist`（后端未变，未重启）。真实页面量测：1600px 下右栏带 15px 滚动条，表格 279px 正好放下，行高 33px、无数字折行；700px 并排时表格 276px、420px 单列时 362px，均无折行、无横向滚动，深色主题同样。对话与抽屉里的 `MarketCard` 其他形态不变。仍未处理：700px 下账户卡「浮动盈亏」金额折行。

## v3.2.1 工作台前端（2026-11-09 已部署）

`Home.svelte` 是工作台根组件，浏览器前进 / 后退可用：

| hash | 中间区域 |
| --- | --- |
| `#` | 最近对话；没有对话时显示新开空态与五个快捷提问 |
| `#chat/{chat_id}` | 指定对话 |
| `#today`、`#today?session=YYYY-MM-DD` | 今日操作与指定日期简报 |
| `#account`、`#market` | 账户与流水、完整行情 |
| `#plans`、`#plans/{plan_id}` | 计划与持仓；带 id 时展开变化记录 |
| `#research`、`#research/{research_id}` | 研究；带 id 时展开变化记录 |
| `#retrospect`、`#jobs` | 复盘、定时任务 |
| `#_ui` | 仅开发模式的组件画廊 |
| 未知 hash | 默认对话 `#` |

计划 / 研究列表首屏之外的对象会单独读取。

对话卡片用 SSE 首帧的 agent_session 与 tool_end 的 call:N 调用 calls 接口；历史回放在卡片进入视口时并发取结果，统一放在回答文字之上。历史 SQL NULL 的 empty 提示、成功 JSON null 的普通空结果、截断结果与取卡失败都有独立提示，不影响文字阅读。页面对象可带入上下文芯片，芯片按固定前缀随原文存储；快捷入口可直接发送市场 / 今日操作 / 持仓 / 待入场问题，研究入口只预填。切视图、收起侧览与切对话均保留当前 SSE；刷新或关闭页面仍会取消该轮，取消按钮按原对话 id 请求。

所有写入口沿用 preview → 复述确认 → confirmed，完成后共享 store 失效重取并保留简报日期、复盘周与分页。已观察到写工具完成的轮次即使随后出错、取消或断流，也在最终清理时统一刷新一次；只见调用开始不视为已写入，取消不表示回滚。本页操作说明不存消息表。`⌘/Ctrl+K` 在任意完整视图返回最近对话并聚焦提问；设置仍为 Sheet。

本版收尾执行三套全量检查；2026-11-09 经用户授权按设计 spec §15 完成真实库迁移、重建 `web/dist`、重载服务与真实 Pi 冒烟，记录见下节。

## v3.2.1 上线四步（2026-11-09 已执行）

依据 [设计 spec](../specs/2026-11-09-v3.2.1-workbench-shell-and-data-views-design.md) §15，以下四步在 2026-11-09 经用户授权（「验收并完成发布」）依次执行，执行记录在本节末尾。

1. **备份真实库并执行 036**：按 [迁移 036 步骤](migrations-and-backup.md#036-与-v321-上线四步2026-11-09-已执行) 先确认只待执行 036、留存工具记录行数，`pg_dump -Fc` 后核对 SHA-256 与 `pg_restore --list`，再运行 `migrate`。验证追踪行、新列类型 / 默认值、旧行数与 NULL 结果；此步通过后再重载新代码。
2. **构建并重载页面服务**（这一步会替换正在服务的前端，收尾测试只用 scratch）：

   ```bash
   git rev-parse HEAD
   cd web && npm run build
   cd ..
   test -s web/dist/index.html
   launchctl kickstart -k gui/$(id -u)/local.investment-assistant
   launchctl print gui/$(id -u)/local.investment-assistant | grep -E 'state|pid'
   curl --fail --silent --show-error --user '<Basic 用户名>' http://127.0.0.1:8787/ -o .local/v3/workbench-index.html
   test -s .local/v3/workbench-index.html
   ```

   未启用 Basic 时去掉 `--user`；启用时交互输入密码，不把密码写进命令或报告。保存构建与重载日志，检查新进程及首页 200；静态资源每次请求从磁盘读取，但本版后端也变了，仍须重载。
3. **真实 Pi 与页面冒烟**：打开 `http://127.0.0.1:8787/#`，确认最近对话；新开对话问「昨天的市场热度怎么样？」，看到 MarketCard 的 `get_market · call:N · {session}` 来源行；刷新后相同引用与数据仍可回放。逐个打开仪表 / 行情 / 定时任务侧览、七个完整视图，切浅深主题并用窄屏验证 Sheet 互斥。保留对话 id、agent_session、call:N 与界面证据；此步会使用真实运行时并写入对话 / 调用记录。

   ```bash
   curl --fail --silent --show-error --user '<Basic 用户名>' http://127.0.0.1:8787/api/market -o .local/v3/workbench-market.json
   curl --fail --silent --show-error --user '<Basic 用户名>' http://127.0.0.1:8787/api/runs -o .local/v3/workbench-runs.json
   curl --fail --silent --show-error --user '<Basic 用户名>' http://127.0.0.1:8787/api/ledger -o .local/v3/workbench-ledger.json
   .venv/bin/python - <<'CHECK'
   import json
   from pathlib import Path
   def read(name):
       return json.loads(Path(f'.local/v3/workbench-{name}.json').read_text())
   assert {'session', 'calc_version', 'label', 'breadth', 'etf_table', 'watch'} <= read('market').keys()
   runs = read('runs')
   assert {'schedule', 'runs'} <= runs.keys()
   assert {'weekdays', 'hour', 'minute', 'tz', 'next_at'} <= runs['schedule'].keys()
   assert runs['schedule']['tz'] == 'Asia/Shanghai'
   assert runs['schedule']['next_at'].endswith('+08:00')
   assert {'trades', 'cash_flows', 'equity', 'voids'} <= read('ledger').keys()
   print('workbench API shapes verified')
   CHECK
   ```

   另用浏览器 Network 核对 calls 响应 `{ ref, tool, args, result, error, called_at, truncated, result_available }` 与卡片一致。完整形状见本页接口清单与 spec §11。若没有成功运行，market 应为 404 `no_run`，runs 仍含 schedule；这只验证空库路径，尚不能通过真实 MarketCard 冒烟，不为此自动触发每日任务。
4. **记录通过并发布**：前三步通过后，把备份校验、036 applied 输出、构建 HEAD、服务 pid、API 与真实 Pi 回放证据补到本文与迁移文档；将 CHANGELOG 的 v3.2.1 未发布段改为实际日期，更新 roadmap 的 v3.2 行。验证：

   ```bash
   git diff --check
   env -u INVESTMENT_ASSISTANT_DATABASE_URL -u INVESTMENT_ASSISTANT_TEST_DATABASE_URL -u TEST_DATABASE_URL .venv/bin/python -m pytest tests/test_docs_links.py -q
   git diff -- CHANGELOG.md docs/roadmap.md docs/operations/local-service.md docs/operations/migrations-and-backup.md
   ```

   推送与打 tag 仍需各自授权。

**执行记录（2026-11-09，真实库与本机服务）：**

- 上线前三套全量：离线 910 passed / 412 skipped，一次性库 1322 passed，前端 68 个文件 472 passed；`npm run build` 的 svelte-check 943 个文件 0 错误 0 警告。
- 第 1 步：迁移 036 已执行，备份与核对见 [迁移文档](migrations-and-backup.md#036-与-v321-上线四步2026-11-09-已执行)。
- 第 2 步：构建 HEAD `97c5e53`，`web/dist/index.html` 非空；`launchctl kickstart -k` 后服务 pid 70480 → 29603，`state = running`，首页 200（未启用 Basic）。
- 第 3 步接口：`/api/market` 200（`session` 2026-11-06、`calc_version` `regime-2`、`label` neutral、`watch` 5 只）；`/api/runs` 200（8 次运行，`schedule` 周二至周六 11:30 `Asia/Shanghai`，`next_at` `2026-11-10T11:30:00+08:00`）；`/api/ledger` 200（成交 1、出入金 1、对账 0、作废 0）；`/api/today.counts` 为 `{actions 1, holdings 1, pending 3, watching 1}`；上面的形状校验脚本输出 `workbench API shapes verified`。
- 第 3 步真实 Pi：`#` 打开最近对话、抽屉缺省「仪表」。新开对话 `f341edc74edf4196a3890b81bedc5536` 点快捷提问「昨天市场热度」，先出现「调用中」占位，随后换成 MarketCard，来源行 `get_market · call:1 · 2026-11-06 · regime-2`；助手消息 `agent_session = chat-f341edc74edf4196a3890b81bedc5536-1`、`tool_refs = ['call:1']`；`v3_tool_calls` 该行工具名为裸名 `get_market`、`result` 2182 字节、`truncated` 为假；calls 接口 200、`result_available` 为真，换成别的对话的 `agent_session` 得到 404 `unknown_call`。对话标题写成「昨天的市场热度怎么样？」并归入「今天」。刷新后回放出同一张卡（同一 `call:1`），在回答文字之上。
- 第 3 步页面：七个 hash（含 `#plans/1`）都渲染对应标题，未知 hash 回到对话；仪表 / 行情 / 定时任务三个侧览都能打开；深色主题生效；900px 宽度下左栏与侧览变成 Sheet，打开左栏时只有一个对话框；控制台无错误。
- 已知外观问题（不阻断发布）：288px 侧览里的 ETF 表数字会折行（如 `8.1` / `9%`）；窄屏左栏 Sheet 比栏宽，右侧留白。

## 常用操作

```bash
# 加载（仅在服务未加载时使用；当前服务已运行）
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.investment-assistant.plist
```

```bash
# 停止（卸载当前加载的服务；plist 保留，下次登录仍会加载）
launchctl bootout gui/$(id -u)/local.investment-assistant
```

```bash
# 彻底卸载（停止并删除 plist，之后登录不再启动）
launchctl bootout gui/$(id -u)/local.investment-assistant
rm ~/Library/LaunchAgents/local.investment-assistant.plist
```

服务在运行时：

```bash
# 重启（改了 .env 之后）
launchctl kickstart -k gui/$(id -u)/local.investment-assistant

# 看服务是否在跑
launchctl print gui/$(id -u)/local.investment-assistant | grep -E 'state|pid'

# 健康检查：首页应返回 200（配置了密码时加 -u 用户名:密码）
curl -i http://127.0.0.1:8787/
```

前端构建产物在每次请求时从磁盘读取，`cd web && npm run build` 之后不必重启。`web/dist` 不入库。

## 路径

| 用途 | 本机位置 |
| --- | --- |
| 服务日志 | `~/Library/Application Support/investment-assistant/logs/service.stdout.log`、`service.stderr.log`（plist 里的 `StandardOutPath` / `StandardErrorPath`） |
| 事实任务草稿目录 | `INVESTMENT_ASSISTANT_RUNTIME_BASE` 下的 `briefs/`，本机为 `~/Library/Application Support/investment-assistant/briefs/`，见 [v3-facts.md](v3-facts.md) |
| 配置文件（`INVESTMENT_ASSISTANT_CONFIG`） | `config/local.json` |
| 迁移前备份、验收证据 | `.local/`（不入库） |

## 认证

配置 `SERVER_PWD`（或 `HERMES_DASHBOARD_PASSWORD`，前者优先）后，全部路径都要 HTTP Basic，用户名取 `HERMES_DASHBOARD_USER`（默认 `jianjustin`）。密码为空即无认证，只适合只监听 `127.0.0.1` 的本机服务。`start.command` 启动时地址被强制为 `127.0.0.1`；直接运行 `python -m investment_assistant.api.server` 并把 `HERMES_DASHBOARD_HOST` 设成非本机地址时，需要同时设置 `HERMES_DASHBOARD_ALLOW_PUBLIC=1` 和密码，否则拒绝启动。
