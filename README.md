# Investment Assistant

美股投研工作台。每天早上推送一份交易计划到 Discord，计划的价位能追溯到规则和数据；由你决定是否采纳、自己下单并录入成交，系统再按计划和实际结果做复盘。规则主计划之外，还并列一条 Agent 建议，两条轨道各自做影子跟踪。

不接券商，不自动下单。所有规则都**未经验证**（Phase 0 Gate HOLD），系统输出的价位和仓位只是你所设规则的算术结果，不构成投资建议。

v3 重做进行中，路线与进度见 [docs/roadmap.md](docs/roadmap.md)。旧系统（v2.15）在切换到 v3 前照常运行。v2 的路线、功能与架构文档已归档到 [docs/archive/v2/](docs/archive/v2/)，只读；各版变更见 [CHANGELOG](CHANGELOG.md)。

## 组成

- **后端**：FastAPI + uvicorn 单进程，内置调度线程；PostgreSQL 是唯一的状态存储。
- **代码布局**：`core → data → derivation → execution → analysis → review → optimize`，由 `workflow` 编排、`views` 组装跨层视图，API / tasks 显式注入通知端口。数据库核对入口为 `python -m investment_assistant.workflow check`，概念校验为 `python -m investment_assistant.core.knowledge check`。
- **前端**：Svelte 5，主线入口是「今日」「标的」「复盘」「数据」。
- **外部数据**：yfinance、SEC EDGAR、FRED；Agent 用 LangGraph + 你的 ChatGPT 订阅（设置里一次性登录）；通知走 Discord。
- **运行**：本机 macOS LaunchAgent，只监听 `127.0.0.1:8787`。

详见 [架构](docs/archive/v2/architecture.md)。

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
```

```bash
cd web && npm install && npm run build && cd ..
```

配置 `.env`、执行迁移、建 Agent 检查点表，见 [上手指南](docs/getting-started.md)；然后启动：

```bash
./start.command
```

## 测试

```bash
env -u INVESTMENT_ASSISTANT_DATABASE_URL -u INVESTMENT_ASSISTANT_TEST_DATABASE_URL -u TEST_DATABASE_URL .venv/bin/python -m pytest tests/ -q
```

```bash
cd web && NODE_OPTIONS=--no-experimental-webstorage npm test && npm run build
```

## 文档

从 [docs/README.md](docs/README.md) 开始：架构、功能、运维、roadmap、spec 与 plan。
