# 本机服务

本机服务是 v3 保留下来的**外壳**：一个只托管前端构建产物的 HTTP 服务。首页是占位页（「今日简报与对话在 v3 第 3 步实现」），没有调度线程，没有业务接口，不读写数据库。v2 的调度、任务、通知、Agent 连接都随旧系统移除（见 [roadmap](../roadmap.md) 第 5 步；原来的运维说明在 [archive/v2/operations/](../archive/v2/operations/scheduling-and-notifications.md)）。每日的采集与名单由命令行的 `facts run` 完成，见 [v3-facts.md](v3-facts.md)。

## 当前状态

**服务现在没有在运行。** 2026-11-06 移除 v2 之前，已执行 `launchctl bootout gui/$(id -u)/local.investment-assistant` 停掉旧服务（它设了 `KeepAlive`，不停会在删了一半的代码上继续跑定时任务）。plist 文件没有动，也没有重新加载：**下次登录时 launchd 会重新加载它并启动外壳**，这是无害的，外壳只显示占位页。

## 启动链路

```
~/Library/LaunchAgents/local.investment-assistant.plist（不在仓库里）
  Label = local.investment-assistant，RunAtLoad + KeepAlive，WorkingDirectory = 仓库根目录
  └─ ./start.command
       └─ .venv/bin/python scripts/start_local.py
            读取仓库 .env（缺文件就退出）；强制监听 127.0.0.1
            └─ investment_assistant.api.server（uvicorn，端口 HERMES_DASHBOARD_PORT，默认 8787）
```

服务只处理这些请求：

- `/` 返回 `web/dist/index.html`（占位页）；`/assets/…` 返回构建产物里的文件。
- 其余路径，以及 `POST` / `PUT` / `PATCH` / `DELETE`，一律 404 `{"error": "not found"}`。
- 这两类兜底（`GET` 的静态托管与其他方法的 404）是 `create_app()` 里最后注册的两个路由；新的 `/api/...` 路由必须在它们之前 `include_router`，否则会被拦成 404。
- 没有 `web/dist` 时 `/` 返回 503 `{"error": "frontend_not_built", …}`。
- 组件画廊（`#_ui`）只在开发模式（`cd web && npm run dev`）可用，构建产物不含它。

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
