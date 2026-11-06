# Investment Assistant

美股投研助手，v3 重做进行中。目标是让入口变成 agent：每天一份简报回答「最近行情适合交易什么」，落到个股，之后可以在同一个工作区对话追问。

不接券商，不自动下单。所有规则都**未经验证**，系统输出的价位和仓位只是你所设规则的算术结果，不构成投资建议。

旧系统（v2）已在 2026-11-06 移除，完整保留在 git 历史里（最后一个含 v2 的提交是 `d082400`）；它的路线、功能、架构与概念卡文档归档在 [docs/archive/v2/](docs/archive/v2/)，只读。各版变更见 [CHANGELOG](CHANGELOG.md)。

## 现在能做什么

- **事实任务**：`python -m investment_assistant.facts run` 采集八族数据，用规则算出个股名单，落一条运行记录和一份草稿，全程不调模型；`facts show` 打印草稿。
- **MCP 工具面**：`python -m investment_assistant.toolsurface` 以 stdio 给模型提供读事实、算计划价位、保存结论与计划的工具（brief / chat 两种模式）。
- **服务外壳**：`./start.command` 启动一个只托管前端构建产物的服务，首页是占位页；每日简报的推送和页面还没做（v3 第 2、3 步）。

PostgreSQL 是唯一的状态存储；外部数据来自 yfinance、SEC EDGAR、FRED。

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd web && npm install && npm run build && cd ..
```

配置 `.env`、执行迁移、跑第一次事实任务，见 [上手指南](docs/getting-started.md)。

## 路线

v3 各步骤与进度见 [docs/roadmap.md](docs/roadmap.md)；设计见 [docs/specs/](docs/specs/)。

## 测试

三套：离线 pytest、一次性库 pytest、前端 `npm test && npm run build`，命令见[上手指南的测试一节](docs/getting-started.md#测试)。

## 文档

从 [docs/README.md](docs/README.md) 开始：roadmap、spec、plan、运维与设计标准。
