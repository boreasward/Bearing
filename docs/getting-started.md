# 上手指南

从零在本机跑起来：安装、配置、建库、运行、跑测试。日常运维见 [operations/](operations/local-service.md)。

## 环境与安装

前置条件：

- macOS（本机服务用 LaunchAgent）
- Python 3.11+（`.venv` 现用 3.11）
- Node.js（构建 `web/` 前端并跑前端测试；Node 26 下不需要额外环境变量，只会打印 localStorage 的实验性警告）
- PostgreSQL（本机用 Homebrew `postgresql@17`）
- SEC EDGAR 用户标识（`名字 邮箱`）

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd web && npm install && npm run build && cd ..
```

`requirements.txt` 已含 `pytest`。`npm run build` 产出 `web/dist`，外壳服务托管的就是它；`web/dist` 不入库。

## 配置

### `.env`（仓库根目录，不入库）

`facts` 和外壳服务启动时会读取它；`toolsurface` 只在给了 `--env-file` 时读，否则用进程环境；`migrate` 不读 `.env`，连接串要在进程环境里给。代码实际读取的变量：

| 变量 | 必需 | 说明 |
| --- | --- | --- |
| `INVESTMENT_ASSISTANT_DATABASE_URL` | 是 | PostgreSQL 连接串；`facts`、`toolsurface` 和 `python -m investment_assistant.migrate` 都读它 |
| `SEC_USER_AGENT` | 是 | `名字 邮箱`；缺失或不含 `@` 时，走 EDGAR 的采集（申报列表、基本面、行业）会失败 |
| `INVESTMENT_ASSISTANT_CONFIG` | 否 | 配置文件路径，本机为 `config/local.json`；不设则读 `/opt/hermes-investment-assistant/config/investment-assistant.json`，文件不存在就全用默认值 |
| `INVESTMENT_ASSISTANT_RUNTIME_BASE` | 否 | 草稿目录的上级：草稿落在 `<此目录>/briefs/`；不设则落在仓库本地 `.local/v3/briefs/`；本机为 `~/Library/Application Support/investment-assistant` |
| `FRED_API_KEY` | 否 | FRED 利差；未配置时事实任务记「未配置」 |
| `INVESTMENT_ASSISTANT_DB_POOL_MAX` | 否 | 连接池上限，默认 8 |
| `HERMES_DASHBOARD_PORT` | 否 | 外壳服务端口，默认 `8787` |
| `HERMES_DASHBOARD_USER`、`SERVER_PWD` | 否 | 外壳服务的 HTTP Basic 认证；用户名默认 `jianjustin`，密码也可用 `HERMES_DASHBOARD_PASSWORD`（`SERVER_PWD` 优先）；密码为空即无认证 |
| `HERMES_DASHBOARD_HOST`、`HERMES_DASHBOARD_ALLOW_PUBLIC` | 否 | 绑定地址与公网绑定开关；经 `start.command` 启动时地址被强制为 `127.0.0.1` |

### 配置文件（`config/local.json`，示例见 `config/investment-assistant.example.json`）

配置文件里不认识的键一律忽略。当前被读取的段落：

- `v3`：事实任务与规则的参数，全部可选，缺省即 spec 冻结值；每个键的含义与默认值见 [operations/v3-facts.md](operations/v3-facts.md)（`top_n`、`collect_workers`、`sec_workers`、`replay_sessions`、`hold_sessions`、`min_triggers`、`win_hi` / `win_lo`、`min_group`、`sector_etfs`、`briefs_dir`）；v3.3 新增四个换仓键：`rotation_cooldown_sessions`（5，换仓冷却的交易日数）、`rotation_min_fill`（0.5，「钱不够」的比例线）、`rotation_fee_trades`（20，手续费估计取最近几笔成交）、`rotation_horizons`（`[5, 10, 20]`，复盘对照的三个时点），取值与范围见 [operations/v3-facts.md](operations/v3-facts.md)。
- `workbench`：`risk_pct`（默认 0.01）、`single_cap`（0.25）、`total_cap`（**0.6**，v3.3 起由 1.0 改为 0.6：持仓总市值占总资产的上限，同时是换仓比较的触发线；本机 `config/local.json` 没有设这个键，改默认值即生效），计算计划股数时的仓位参数。
- `filings`：`forms`（默认 10-Q、10-K）、`output_dir`；`sec_monitor`、`notify`：库里保留的 SEC 监控与 Discord 发送通道读取，目前没有入口调用。

采集范围是 `config/universes/nasdaq100.json`（相对运行目录，请在仓库根目录运行命令）；关注列表读数据库表 `watchlist_items`。

### 关注列表

`facts` 每次运行读 `watchlist_items`（读 `ticker`、`status`、`tags` 三列）：`status = 'active'` 且代码是合法美股的行组成「股票池」。股票池的作用有两处：

- 采集：每日八族里，除指数成分与宏观两族（目标固定）外，其余六族的采集目标都包含股票池与持仓；日线、财报日、行业归类三族另外并入 `nasdaq100.json` 的全部成分与基准、行业 ETF。
- 规则：算名单的股票范围是 `nasdaq100.json` 的成分与股票池的并集。

标签是可选的：`core` 把一只票放进核心池（最多 15 只），`sector:<ETF>`（`SMH`、`XLK`、`QQQ` 之一，至多一个）标注行业 ETF；股票池总数不超过 100。关注列表里没有 `active` 的美股，或标签、数量超限时，`facts` 当作没有股票池继续运行，不报错。

**增删关注列表走对话工具**：v3.1 起对话里有 `watchlist_add` / `watchlist_remove` 两个工具（先复述、确认后才写），除此之外没有别的命令能改它（`toolsurface` 的读写工具不碰它）。要直接写 SQL 时，表的必填列只有 `ticker`（唯一），`status` 默认 `active`，`tags` 默认空：

```sql
-- 加入关注
INSERT INTO watchlist_items (ticker) VALUES ('AAPL');

-- 归档（status 取 active / paused / archived；非 active 的行不进股票池）；表上没有触发器，updated_at 要自己写
UPDATE watchlist_items SET status = 'archived', updated_at = now() WHERE ticker = 'AAPL';

-- 彻底删除
DELETE FROM watchlist_items WHERE ticker = 'AAPL';
```

v2 的写入实现（`upsert_watchlist_item`、`delete_watchlist_item`）曾随旧系统删除，v3.1 用新的对话工具补回了这个写入口。

## 建库

迁移文件在 `migrations/`（001–037），`migrate` 入口按文件名顺序执行未执行过的迁移，并记入 `schema_migrations`，重复运行不做任何事：

```bash
INVESTMENT_ASSISTANT_DATABASE_URL=postgresql://... .venv/bin/python -m investment_assistant.migrate
```

输出一行 JSON：`{"applied": [...], "count": N}`。新建的空库直接运行上面这条即可，一路应用到 037。**对已在用的真实库，迁移 037（v3.3）尚未执行，需用户单独授权，步骤见 [operations/migrations-and-backup.md](operations/migrations-and-backup.md#037-与-v33-上线五步未执行)；不要用上面这条直接对真实库执行。**

**`migrate` 在已有 v2 数据的库上会拒绝执行**：033 待执行、且它要删的表里有任何一张存在并且有行时，整个 `migrate` 退出、一条迁移都不应用，需要设置 `INVESTMENT_ASSISTANT_ALLOW_DROP_V2_TABLES=1` 确认；见 [operations/migrations-and-backup.md](operations/migrations-and-backup.md)。

**033 会删除 v2 的全部业务表**（账户、成交、计划、复盘、Agent 连接等 57 张表，外加 LangGraph 的四张检查点表，有就删），事实表与 `v3_` 表保留。新库跑完整条链没有影响；**已有数据的库在执行前先读 [operations/migrations-and-backup.md](operations/migrations-and-backup.md)**，按其中的步骤先备份。数据库测试用的一次性库的搭建方法也在那里。

## 运行

在仓库根目录：

```bash
# 事实任务：采集八族数据，规则算出个股名单，落一条运行记录和一份草稿；不调模型
.venv/bin/python -m investment_assistant.facts run
.venv/bin/python -m investment_assistant.facts show

# MCP 工具面：一个进程服务一个交易日、一种模式（brief / chat），stdio JSON-RPC
.venv/bin/python -m investment_assistant.toolsurface --session 2026-11-05 --mode brief --agent-session <会话编号> --env-file .env

# 每日任务：采集与事实计算、计划复查、简报会话与推送（五件事见 operations/daily-run.md）
.venv/bin/python -m investment_assistant.daily run
.venv/bin/python -m investment_assistant.daily settings show    # 运行时与模型设置（show / options / set）

# 外壳服务：托管前端页面与页面接口
./start.command
```

- `facts run` 的参数、退出码、草稿位置与实测耗时见 [operations/v3-facts.md](operations/v3-facts.md)；`toolsurface` 的两条运维要求也在那里，Pi 客户端的接法见 [operations/pi-unattended.md](operations/pi-unattended.md)。
- `daily run` 的退出码、同日重跑与补查、预算与重试、LaunchAgent 的安装见 [operations/daily-run.md](operations/daily-run.md)。
- `start.command` 启动的服务托管 v3.1 的页面（今日操作、今日简报、计划与持仓、对话，另有账户条与设置面板）及其 `/api/...` 接口；接口清单与认证见 [operations/local-service.md](operations/local-service.md)。打开 `http://127.0.0.1:8787`。
- 组件画廊只在开发模式可用：`cd web && npm run dev`，再打开 `#_ui`。

## 测试

三套，只在改动收尾时各跑一次，日常只跑与改动相关的测试文件：

```bash
# 1. 后端离线回归：去掉数据库环境变量，数据库测试自动跳过
env -u INVESTMENT_ASSISTANT_DATABASE_URL -u INVESTMENT_ASSISTANT_TEST_DATABASE_URL .venv/bin/python -m pytest -q

# 2. 后端一次性库回归：数据库测试在独立 schema 里建表、结束后删除
INVESTMENT_ASSISTANT_TEST_DATABASE_URL=postgresql://test@127.0.0.1:55433/workbench_test \
  env -u INVESTMENT_ASSISTANT_DATABASE_URL .venv/bin/python -m pytest -q

# 3. 前端
cd web && npm test && npm run build
```

`INVESTMENT_ASSISTANT_TEST_DATABASE_URL` **必须指向一次性库，永远不要指向真实库**；搭建方法见 [operations/migrations-and-backup.md](operations/migrations-and-backup.md#一次性测试库)。

## 常见问题

- **`facts` 退出码 2「数据库不可用」**：没有设置 `INVESTMENT_ASSISTANT_DATABASE_URL`、数据库连不上，或事实表缺失（`price_bars`、`news_items`、`news_item_tickers`、`filings`、`earnings_dates`、`watchlist_items`、`macro_observations` 缺一张就判不可用）。迁移不会自动执行。
- **yfinance 偶发空数据**（如 `^VIX`）：记为非致命错误，下一次运行通常恢复。
- **首页返回 `frontend_not_built`**：没有 `web/dist`，先 `cd web && npm run build`。
