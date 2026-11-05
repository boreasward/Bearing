# 上手指南

从零在本机跑起来：安装、配置、建库、启动、跑测试。日常运维见 [operations/](operations/local-service.md)。

## 前置条件

- macOS（本机服务用 LaunchAgent）
- Python 3.11+
- Node.js（构建 `web/` 前端；Node 26 跑测试时需 `NODE_OPTIONS=--no-experimental-webstorage`）
- PostgreSQL（本机用 Homebrew `postgresql@17`）
- SEC EDGAR 用户标识（`名字 邮箱`）

## 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install pytest
cd web && npm install && npm run build && cd ..
```

## 配置

### `.env`（仓库根目录，不入库）

| 变量 | 必需 | 说明 |
| --- | --- | --- |
| `INVESTMENT_ASSISTANT_DATABASE_URL` | 是 | PostgreSQL 连接串；缺失时大多数接口降级，调度线程不启动 |
| `SEC_USER_AGENT` | 是 | `名字 邮箱`；SEC 列表、下载与监控都要用 |
| `INVESTMENT_ASSISTANT_CONFIG` | 否 | 配置文件路径，本机为 `config/local.json` |
| `INVESTMENT_ASSISTANT_RUNTIME_BASE` | 否 | 运行日志所在目录，默认 `/opt/hermes-investment-assistant`；本机为 `~/Library/Application Support/investment-assistant` |
| `FILINGS_REPORT_DIR` | SEC 监控需要 | SEC 正文报告输出目录 |
| `SCHEDULER_ENABLED` | 否 | `0` 关闭调度线程；本机入口以 `.env` 中的值为准 |
| `HERMES_DASHBOARD_PORT` | 否 | 端口，默认 `8787` |
| `HERMES_DASHBOARD_USER`、`SERVER_PWD` | 否 | HTTP Basic 认证；本机只监听 `127.0.0.1` |
| `DISCORD_WEBHOOK_SIGNALS`、`DISCORD_WEBHOOK_EARNINGS` | 否 | 各频道 webhook；也可在「设置 → 推送配置」填写，页面配置优先 |
| `SEC_DISCORD_BOT_TOKEN` | SEC 监控需要 | SEC 监控用 Bot 身份直接发 Discord |
| `SMTP_PASSWORD` | 选邮件渠道时 | 邮件渠道密码 |
| `FRED_API_KEY` | 否 | FRED 利差；未配置时运行报告记「未配置」 |
| `WORKBENCH_AGENT_PROVIDER` / `WORKBENCH_AGENT_DAILY_TOKENS` | 否 | Agent 开关（`chatgpt` 默认，`off` 关闭）、每日 token 上限；Agent 用你的 ChatGPT 订阅，没有 API 密钥，也不在 `.env` 里选模型：在「设置 → Agent 连接」登录并选模型，未连接时 Agent 轨道记 `not_connected` 停下，规则轨道照常 |
| `WORKBENCH_CHATGPT_CALLBACK_PORT` | 否 | 固定 ChatGPT 登录回调监听的本机端口；默认由系统分配 |

### 配置文件（`config/local.json`，示例见 `config/investment-assistant.example.json`）

- `workbench`：`risk_pct`（单笔风险，默认 0.01）、`single_cap`（单票上限 0.25）、`total_cap`（总持仓上限 1.0）、`public_url`（推送链接前缀，默认 `http://localhost:8787`）、`return_tiers`（月度分档，默认 `[0.20, 0.10, 0.0]`）。参数会冻结进每份计划。
- `filings`、`sec_monitor`：SEC 下载与监控，见 [features/sec-filings.md](archive/v2/features/sec-filings.md)。
- `watchlist`：只给 v1 遗留任务用；工作台的观察池来自数据库观察名单。

## 建库

按 [operations/migrations-and-backup.md](operations/migrations-and-backup.md) 执行迁移 `001`–`021`，再建 Agent 检查点表：

```bash
.venv/bin/python -m investment_assistant.analysis.agent setup
```

然后在「设置 → 关注列表」添加美股并勾选核心池（≤ 15 只）。

要启用 Agent 轨道，启动服务后在「设置 → Agent 连接」点「连接 ChatGPT」，在本机浏览器里完成登录，再选一个模型（不连接也能用：规则轨道照常，Agent 记 `not_connected`）。步骤与排错见 [operations/local-service.md](operations/local-service.md) 的「Agent 连接」，Agent 怎么工作见 [features/agent-track.md](archive/v2/features/agent-track.md)。

## 启动

```bash
./start.command
```

打开 `http://127.0.0.1:8787`，OpenAPI 在 `/docs`。本机常驻运行用 LaunchAgent，见 [operations/local-service.md](operations/local-service.md)。

## 测试

```bash
# 后端离线回归：去掉数据库环境变量，数据库测试自动跳过
env -u INVESTMENT_ASSISTANT_DATABASE_URL -u INVESTMENT_ASSISTANT_TEST_DATABASE_URL -u TEST_DATABASE_URL .venv/bin/python -m pytest tests/ -q

# 前端
cd web && NODE_OPTIONS=--no-experimental-webstorage npm test && npm run build
```

数据库测试只能连一次性库，搭建方法见 [operations/migrations-and-backup.md](operations/migrations-and-backup.md#一次性测试库)。不要把测试库 URL 指向真实库。

## 常见问题

- **页面显示「数据库未就绪」**：工作台接口返回 503，说明迁移没跑到位；不会自动迁移。
- **yfinance 偶发空数据**（如 `^VIX`）：记为非致命错误，下一次运行通常恢复。
- **资讯条数为 0**：运行报告 `news.fetched` 为 0 时，说明 yfinance 搜索接口再次失效。
- **Discord 没收到**：看运行报告的 `push`；检查「设置 → 推送配置」里该任务是否启用、频道是否有 webhook。
