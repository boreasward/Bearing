# 本机服务

v2.0.0 只支持本机运行：一台 macOS，一个用户级 LaunchAgent，一个 PostgreSQL。

## 启动链路

```
~/Library/LaunchAgents/local.investment-assistant.plist（不在仓库里）
  RunAtLoad + KeepAlive，WorkingDirectory = 仓库根目录
  └─ ./start.command
       └─ .venv/bin/python scripts/start_local.py
            读取仓库 .env；SCHEDULER_ENABLED 以文件为准（默认 1）；强制监听 127.0.0.1
            └─ investment_assistant.api.server（uvicorn，端口 HERMES_DASHBOARD_PORT，默认 8787）
```

- 用户登录时启动，退出登录后停止；电脑需要保持开机和唤醒，定时任务才会按时跑。
- 调度线程随 API 进程启动；下次运行时间为空的任务在开启调度后立即执行一次，不会逐日补跑历史缺口。

## 常用操作

```bash
# 重启（改了 .env、拉了新代码、重建了前端之后）
launchctl kickstart -k gui/$(id -u)/local.investment-assistant
```

```bash
# 看服务是否在跑
launchctl print gui/$(id -u)/local.investment-assistant | grep -E 'state|pid'
```

```bash
# 健康检查
curl -s http://127.0.0.1:8787/api/status
```

前端改动后要先 `cd web && npm run build`，再重启服务。`web/dist` 不入库。

## Agent 连接

Agent 轨道用你的 ChatGPT 订阅（Sign in with ChatGPT），没有 API 密钥。连接状态存在数据库的 `agent_connections`（迁移 028），在「设置 → Agent 连接」管理。

- **一次性登录**：点「连接 ChatGPT」，页面返回一个授权地址并在新标签页打开；登录要在**运行服务的这台 Mac 的浏览器里**完成，因为授权结果会回到这台机器的 `127.0.0.1:<随机端口>/auth/callback`（服务临时开的一个独立监听，只收一次回调，10 分钟后或点「取消」就关闭；服务的 8787 端口挂着 HTTP Basic 认证，回调请求带不了凭据，所以不用它）。端口默认由系统分配；需要固定时在 `.env` 设 `WORKBENCH_CHATGPT_CALLBACK_PORT`（改 `.env` 要重启服务，见上面的「常用操作」）。登录等待期间重启服务会使这次登录作废，重新点「连接」即可。
- **代理**：服务读取环境里的 `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` 访问 `auth.openai.com` 与 `api.openai.com`；LaunchAgent 不继承终端里的代理变量，需要代理时写进 `.env`。回环监听本身不走代理。
- **自动续期**：每个 Agent 单元用令牌前检查剩余时间（不足 300 秒就刷新），refresh token 每次刷新滚动 30 天，所以每天运行一次连接不会过期。连续 30 天以上没有运行（电脑关机、服务停用）才会失效，失效后设置页出现红条「ChatGPT 连接已失效，请重新连接」，点「重新连接」重新登录同一个账号（失效状态下换账号会被拒绝：`account_mismatch`；要换账号，先「断开」再「连接」）。
- **选模型**：登录后在下拉框选；清单来自 ChatGPT 返回的可见模型，不写死。所选模型以后不在清单里，Agent 记 `model_unavailable`，页面提示重新选。
- **已登录但没有共享**：账号没有开启计划用量共享（令牌缺 `chatgpt.tokens.use.direct`）时页面提示「已登录，但这个账号没有开启计划用量共享」，Agent 记 `not_configured`；在 ChatGPT 设置里开启后重新连接。
- **断开**：点「断开」会向 ChatGPT 撤销 refresh token 并清空本机凭据和账号信息；若页面提示「远端断开未确认」，再到 ChatGPT 设置里手动断开这个应用（应用名 `investment-assistant`）。
- **命令行**：`.venv/bin/python -m investment_assistant.core.llm.connection status` 打印连接状态（不含任何令牌）。
- **凭据的安全级别**：令牌明文存在本机数据库，与 `.env` 里的密钥同级；备份数据库就等于备份了凭据，备份文件要当作机密保管。令牌不进日志、不经任何接口回显、不进错误文本。
- **`.env` 键的变化（v2.8）**：Agent 不再读取旧供应商的密钥、`WORKBENCH_AGENT_MODEL`、`WORKBENCH_AGENT_BASE_URL`，这几行可以从 `.env` 删掉；`WORKBENCH_AGENT_PROVIDER` 只认 `chatgpt`（默认）与 `off`；新增可选的 `WORKBENCH_CHATGPT_CALLBACK_PORT`。真实 `.env` 的改动与重启按 v2.8 上线步骤经你授权后进行。

## 路径

| 用途 | 本机位置 |
| --- | --- |
| 服务日志 | `~/Library/Application Support/investment-assistant/logs/service.stdout.log`、`service.stderr.log` |
| 运行日志目录（`INVESTMENT_ASSISTANT_RUNTIME_BASE`） | `~/Library/Application Support/investment-assistant` |
| 配置文件（`INVESTMENT_ASSISTANT_CONFIG`） | `config/local.json` |
| SEC 下载（`filings.output_dir`）与正文报告（`FILINGS_REPORT_DIR`） | 配置文件与 `.env` 中指定的目录 |
| 迁移前备份、验收证据 | `.local/`（不入库） |

`investment_assistant/runtime_paths.py` 里的 `/opt/...`、`/srv/...` 是写死的默认值，只有其中一部分能覆盖：运行日志用 `INVESTMENT_ASSISTANT_RUNTIME_BASE`，配置文件用 `INVESTMENT_ASSISTANT_CONFIG`，SEC 下载目录用配置文件里的 `filings.output_dir`。

## 运行状态从哪里看

- 「工具 → 任务中心」：每个任务的启停、上次/下次运行、立即运行、按日期运行（单日或范围，上限 92 天）。
- 「工具 → 运行记录」：`job_reports`，每 15 秒刷新；点「详情」看 `summary`。
- 「设置 → 系统」：数据库、调度线程、各服务状态；`GET /api/health` 给出各项检查。
- 「设置 → 环境变量」：只显示是否已配置，不回显值。

## 认证

配置 `SERVER_PWD`（或 `HERMES_DASHBOARD_PASSWORD`，前者优先）后，全部路径都要 HTTP Basic，用户名取 `HERMES_DASHBOARD_USER`（默认 `jianjustin`）。密码为空即无认证，只适合只监听 `127.0.0.1` 的本机服务。
