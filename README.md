# Investment Assistant

美股投研工作台。每天早上推送一份交易计划到 Discord，计划的价位能追溯到规则和数据；由你决定是否采纳、自己下单并录入成交，系统再按计划和实际结果做复盘。规则主计划之外，还并列一条 Agent 建议，两条轨道各自做影子跟踪。

不接券商，不自动下单。所有规则都**未经验证**（Phase 0 Gate HOLD），系统输出的价位和仓位只是你所设规则的算术结果，不构成投资建议。

当前版本：**v2.10.0**（2026-10-31），见 [CHANGELOG](CHANGELOG.md)。

v2.10 状态：`engineering_done`、`user_acceptance_pending`（2026-10-31 发布 `v2.10.0` 与修正版 `v2.10.1`，无迁移、无 `.env` 变化；前端界面重构，功能、接口、口径与地址不变；服务已以 v2.10 代码重启；手动验收第 1–6 条通过，验收发现的两项布局问题已修复并随 `v2.10.1` 发布，进度见 [v2.10 验收记录](docs/roadmap/v2.10-acceptance.md)）。实现偏差与已知限制见 [CHANGELOG](CHANGELOG.md)。设计标准见 [docs/design-system.md](docs/design-system.md)。

v2.9 状态：`engineering_done`、`user_acceptance_pending`、`runtime_unverified`（代码已合入 `main`；迁移 029 尚未在真实库执行，服务尚未以 v2.9 代码重启，手动验收未做；上线当天对持仓的两类影响——时间止损的退出指令与止损口径变化——需先用只读脚本核对）。实现偏差与已知限制见 [CHANGELOG](CHANGELOG.md)。

v2.8 状态：`engineering_done`、`user_acceptance_pending`、`runtime_unverified`（代码已合入 `main`；迁移 028 尚未在真实库执行，服务尚未以 v2.8 代码重启，ChatGPT 连接尚未登录，核查脚本 `scripts/siwc_precheck.py` 尚未由你运行，模型层选路按 spec §8.7 先走路径 A、未经核查；连续 10 个交易日定时验收未开始）。实现偏差与已知限制见 [CHANGELOG](CHANGELOG.md)。

v2.7 状态：`engineering_done`、`user_acceptance_pending`（迁移 027 已于 2026-10-29 在真实库执行，服务已以 v2.7 代码重启；手动验收与定时验收进度见 [v2.7 验收记录](docs/roadmap/v2.7-acceptance.md)）。实现偏差与已知限制见 [CHANGELOG](CHANGELOG.md)。

v2.6 状态：`engineering_done`、`user_acceptance_pending`、`runtime_unverified`（没有迁移；代码已合入 `main`，服务尚未重启跑 v2.6 代码，没有真实浏览器验收）。实现偏差与已知限制见 [CHANGELOG](CHANGELOG.md)。

v2.5 状态：`engineering_done`、`user_acceptance_pending`（迁移 026 已于 2026-10-28 在真实库执行并重启；手动验收进行中，见 [v2.5 验收记录](docs/roadmap/v2.5-acceptance.md)；用户验收与连续 10 个交易日定时验收未完成）。实现偏差与已知限制见 [CHANGELOG](CHANGELOG.md)。

v2.4 状态：`engineering_done`、`user_acceptance_pending`（迁移 025 已于 2026-10-27 在真实库执行；手动验收已核对、用户已确认推导可读，连续 5 个交易日定时验收自 10-28 起积累，见[验收记录](docs/roadmap/v2.4-acceptance.md)）。实现偏差与已知限制见 [CHANGELOG](CHANGELOG.md) 与 [roadmap](docs/roadmap/roadmap.md)。v2.3 的定时验收证据仍在积累（见[验收记录](docs/roadmap/v2.3-acceptance.md)）。

## 组成

- **后端**：FastAPI + uvicorn 单进程，内置调度线程；PostgreSQL 是唯一的状态存储。
- **代码布局**：`core → data → derivation → execution → analysis → review → optimize`，由 `workflow` 编排、`views` 组装跨层视图，API / tasks 显式注入通知端口。数据库核对入口为 `python -m investment_assistant.workflow check`，概念校验为 `python -m investment_assistant.core.knowledge check`。
- **前端**：Svelte 5，主线入口是「今日」「标的」「复盘」「数据」。
- **外部数据**：yfinance、SEC EDGAR、FRED；Agent 用 LangGraph + 你的 ChatGPT 订阅（设置里一次性登录）；通知走 Discord。
- **运行**：本机 macOS LaunchAgent，只监听 `127.0.0.1:8787`。

详见 [架构](docs/architecture.md)。

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
