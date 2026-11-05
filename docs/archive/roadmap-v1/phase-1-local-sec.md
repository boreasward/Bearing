# 第一阶段：本地运行并完成 SEC 财报报告

> **已归档（2026-10-23）**：本文属于 roadmap v1，已被 [roadmap v2](../v2/roadmap/roadmap.md) 取代，仅作历史参考，不再更新。

日期：2026-10-14。工作台以本仓库 `main` 为准。不使用 Vibe，不接券商，不自动下单。

## 目标

`investment-assistant` 在这台 Mac 上启动。它按观察名单把 SEC 原文下载到指定目录，在 Postgres 留下索引，并为每份新文件写出一份分析报告。报告只记录文件中的事实、来源和未知项，不给买卖建议。

## 已经具备

- 本机 PostgreSQL 17 已在 `127.0.0.1:5432` 运行，LaunchAgent 为 `homebrew.mxcl.postgresql@17`。没有另起第二个数据库服务。
- 数据库 `investment_assistant` 已创建，所有者是本机用户 `chenjianrui`。本地连接不使用密码。
- 连接串：`postgresql://chenjianrui@127.0.0.1:5432/investment_assistant`
- 迁移 `001` 到 `012` 已应用。财报索引表是 `filings`。
- 下载代码在 `investment_assistant/filings/`。未设置 `SEC_USER_AGENT` 时，下载会静默返回空列表。

## 还没有

- 项目根目录没有 `.env`，API 没有在 `8787` 监听。
- 默认原文目录是 Linux 路径 `/srv/investment-assistant/filings`，不能用作这台机器的目标。
- 默认表单只有 `10-Q` 和 `10-K`。现有投研规则还要含 Item 2.02 或 Exhibit 99.1 的 `8-K`，并且 TSLA 不收 `10-K`。
- `watchlist_items` 还是空的。正式名单仍是笔记库里的 `SEC观察名单.md`。
- 现有 `research/` 是事件研究，不会为一份财报生成分析报告。

## 指定目标

原文：

`/Users/chenjianrui/Library/CloudStorage/OneDrive-个人/笔记库/04-素材/来源库/投资/公司研究/{TICKER}/{FORM}/`

分析报告：

`/Users/chenjianrui/Library/CloudStorage/OneDrive-个人/笔记库/03-领域/投资研究/公司研究/{TICKER}/{FORM}_{filingDate}_{accession}-整理.md`

数据库只保存索引和运行结果，不保存文件正文。已有文件按 `accession_number + primary_document` 跳过，不覆盖旧报告。

## 验收

1. `INVESTMENT_ASSISTANT_DATABASE_URL` 指向上述数据库，`SEC_USER_AGENT` 含姓名和邮箱，`filings.output_dir` 指向上述原文根目录。
2. `SCHEDULER_ENABLED=0` 时启动 `python -m investment_assistant.api.server`，`http://127.0.0.1:8787` 可打开。
3. 观察名单八只股票写入 `watchlist_items`。TSLA 的 `10-K` 限制单独保留。
4. 对一只股票、一个已确定日期窗口执行财报任务。新文件能在原文目录打开，`filings` 有对应行。来源失败时记录错误，不写成零结果。
5. 同一份文件生成一份分析报告。报告包含申报日期、表格类型、accession、原文路径，以及文件中实际出现的营收、利润率和指引。找不到的项写「未披露」。事实和推断分开。不出现买卖、目标价或仓位。

完成这五项之前，不开启定时任务，不配置 Discord，不把 `agentic/` 并进这个服务。
