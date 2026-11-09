# 本机服务

本机服务是 v3 保留下来的外壳：托管前端构建产物，并承载 v3.1 的页面接口（`/api/...`，见下）。没有调度线程：每日的采集与名单由命令行的 `facts run` 完成（见 [v3-facts.md](v3-facts.md)），定时简报由独立的 LaunchAgent 跑（见 [daily-run.md](daily-run.md)）。v2 的调度、任务、通知、Agent 连接都随旧系统移除（见 [roadmap](../roadmap.md)；原来的运维说明在 [archive/v2/operations/](../archive/v2/operations/scheduling-and-notifications.md)）。

## 当前状态

**服务在运行（v3.2 已上线）。** 2026-11-06 移除 v2 之前曾 `launchctl bootout` 停掉旧服务；2026-11-07 v3.1 上线时经用户授权重新 `bootstrap`，页面与 `/api/...` 接口在 `http://127.0.0.1:8787/` 对外。2026-11-09 v3.2 上线：迁移 035 执行后 `npm run build` 重建 `web/dist`、`launchctl kickstart -k` 重启服务；冒烟时 `/api/research`（两组）、`/api/research/{id}`（不存在 404 `unknown_research`）、`/api/retrospect`（空库 `empty` 为真、周界 2026-11-02–11-06）与非法参数的 400 都符合契约，首页「研究」「复盘」两块渲染、控制台无错误。
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
  - `GET /api/today`：今日操作与账户条；v3.2.1 增加 `counts: { actions, holdings, pending, watching }`，分别统计操作、持仓计划、待入场计划与观察中研究；空库四项为 0。
  - `GET /api/market`（v3.2.1）：最新成功运行的市场快照（含 `session`、`calc_version`）与 `watch[]`。关注表按持仓计划 → 待入场计划 → 观察中研究对股票去重，带计划 / 研究 id；两根最近日线给出收盘价与两位小数百分比字符串，缺一根时三个价格 / 涨跌字段均为 null；没有成功运行返回 404 `no_run`。
  - `GET /api/runs?limit=20`（v3.2.1）：按交易日倒序的事实运行与对应简报（含未完成、推送、周报状态），以及配置的 `schedule`；`next_at` 是北京时间、ISO 带 `+08:00`。空库仍返回完整计划表与空 `runs`。`limit` 为 1–100，否则 400 `invalid_argument`。
  - `GET /api/briefs`、`GET /api/briefs/latest`、`GET /api/briefs/{session}`：简报列表与单份简报。
  - `GET /api/plans`、`GET /api/plans/{id}`：计划三组卡片与单份计划（含变化记录）；`POST /api/plans/{id}/stop-order`、`/raise-stop`、`/cancel`：计划写入口。
  - `GET /api/research`（v3.2）：研究两组卡片（`group=watching|ended`、`limit`，观察中缺省）；`GET /api/research/{id}`：单份含变化记录时间线；`POST /api/research/{id}/drop`：放弃研究（先复述，`confirmed` 为真才写）；`GET /api/retrospect`（v3.2）：复盘——执行复盘记录（`limit` 阶梯 20 / 50 / 100）、周报（`week` 切换以前的周）与规则检验两张表。
  - `POST /api/trades`、`/api/cash-flows`、`/api/reconcile`、`/api/voids`、`GET /api/account`：账本（写入口先复述，`confirmed` 为真才写）。
  - `GET /api/ledger?limit=20`（v3.2.1）：成交、出入金、对账、作废记录四张表各最近 `limit` 行，金额与价格为字符串；成交与出入金带 `voided_by` 和 `void_reason`。空库返回四个空数组；`limit` 为 1–100，否则 400 `invalid_argument`。
  - `GET /api/chats`、`POST /api/chats`、`GET /api/chats/{id}`：对话列表、新建、详情。v3.2.1 列表增加 `last_message_at` 并按它倒序（无消息时等于创建时间），消息增加 `agent_session`；`POST /api/chats/{id}/messages`：一轮提问，响应是 `text/event-stream`（SSE），每个事件一帧 `event: <kind>\ndata: <json>\n\n`。首帧 `event: start` 的数据为 `{"kind":"start","agent_session":"chat-…"}`，其余帧保留原字段，末帧必为 `done`/`error`/`cancelled`；`POST /api/chats/{id}/cancel`：取消当轮。对话轮次与每日简报共用一个运行时锁，简报运行期间提问得到 409 `brief_running`。
  - `GET /api/chats/{id}/calls?agent_session=chat-…&ref=call:N`（v3.2.1）：读取保存的 `{ ref, tool, args, result, error, called_at, truncated }`；会话必须以该对话的 `chat-{id}-` 开头，非法编号、其他对话或缺行均返回 404 `unknown_call`。旧行 `result` 为 null；截断结果保持存储的占位对象和标记，工具错误来自保存行的 `error`。
  - `GET /api/settings`、`GET /api/settings/options`、`PUT /api/settings`：运行时与模型设置。
- `/` 返回 `web/dist/index.html`（占位页）；`/assets/…` 返回构建产物里的文件。
- 其余路径，以及未登记的方法，一律 404 `{"error": "not found"}`。
- 这两类兜底（`GET` 的静态托管与其他方法的 404）是 `create_app()` 里最后注册的两个路由；新的 `/api/...` 路由必须在它们之前 `include_router`，否则会被拦成 404（有测试守着这个顺序）。
- 没有 `web/dist` 时 `/` 返回 503 `{"error": "frontend_not_built", …}`。
- 组件画廊（`#_ui`）只在开发模式（`cd web && npm run dev`）可用，构建产物不含它。

## v3.2.1 工作台前端（代码已更新，尚未部署）

`Home.svelte` 是工作台根组件；hash 路由为最近对话 `#`、`#chat/{id}`，以及 `#today`、`#account`、`#market`、`#plans`、`#research`、`#retrospect`、`#jobs`。计划与研究可追加 `/{id}`，展开对应变化记录，列表首屏之外的对象也会单独读取；`#today?session=YYYY-MM-DD` 选择指定日简报。未知 hash 显示默认对话。`#_ui` 仍只在开发模式提供组件画廊。

对话卡片用 SSE 首帧的 agent_session 与 tool_end 的 call:N 调用 calls 接口；历史回放在卡片进入视口时并发取结果，统一放在回答文字之上。旧 null 结果、截断结果与取卡失败都有独立提示，不影响文字阅读。页面对象可带入上下文芯片，芯片按固定前缀随原文存储；快捷入口可直接发送市场 / 今日操作 / 持仓 / 待入场问题，研究入口只预填。切视图、收起侧览与切对话均保留当前 SSE；刷新或关闭页面仍会取消该轮，取消按钮按原对话 id 请求。

所有写入口沿用 preview → 复述确认 → confirmed，完成后共享 store 失效重取并保留简报日期、复盘周与分页。本页操作说明不存消息表。`⌘/Ctrl+K` 在任意完整视图返回最近对话并聚焦提问；设置仍为 Sheet。

本次任务只修改源文件并做聚焦测试，没有执行真实库迁移、替换 `web/dist` 或重载服务。构建验证产物只允许写入一次性 scratch 目录；真实上线仍按设计 spec §15 分步授权。

## 常用操作

```bash
# 重新加载（服务当前已 bootout；要立刻启动外壳而不是等下次登录时用）
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
