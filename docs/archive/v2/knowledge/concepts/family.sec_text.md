---
id: family.sec_text
title: SEC 正文与正文事实
group: family
version: 1
applies_to: [fetch-1, agent-4]
summary: "归档 SEC 申报主文并提取带原文位置的事实；每标的每交易日最多发起3份下载，所有入口共享固定正文根与预算事实。"
related: [family.sec_filings, family.fundamentals, agent.company_researcher, system.adapter_registry, system.cutoff]
sources: [investment_assistant/data/adapters/sec_text.py, investment_assistant/data/sources/edgar.py, investment_assistant/data/repo.py]
---

## 是什么

[SEC 申报列表](family.sec_filings.md)说明「有哪些申报」；本族保存申报主文及带 `quote`、`locator` 的正文事实。工具只读本地已归档内容，不在模型阅读时联网下载。

## 怎么算 / 从哪来

- 主源 EDGAR archives，无备源。选择最近1份10-K、2份10-Q及含 Item 2.02 的8-K，按日期倒序，下载所选申报主文，不扩成全部附件或全历史。
- 元数据写 `filings`，正文事实写 `us_research_observations(dataset='filings')`。正文按 `downloaded_at`、事实按 `first_seen_at` 过滤截止时间；最近10-Q/10-K披露日超过120天标滞后。
- 所有入口注入有效的 `config.filings.output_dir`，共用同一持久化正文目录。其 `.budgets/<ticker>-<session>.json` 在文件锁内、下载前预留额度：每标的每交易日最多3份，失败或中断也不释放名额；daily、手动、研究、补查与 SEC 监控跨 scope 共享。
- 已存在正文或索引不重复下载、不扣新名额；可回补索引/事实，但不算新下载。超时记目标失败并写同 session 阻断，次 session 再试；预算文件须随正文保留，不能在运行间清理。
- SEC 监控仍保留观察名单扫描与通知；其正文处理使用本族、`scope='sec_monitor'`，采集按族与 scope 记行。新正文适配器允许10-K，旧监控的默认发现行为不因此改变。

## 在系统里哪里看到

「数据 → SEC 正文」的原文清单与事实摘要；推理引用展开可看节选、哈希与字节范围。研究工具找不到指定节就报缺口，不猜原文。

## 注意

下载成功不等于提取完整，更不等于事实已经被验证。未存档、未定位或超时要明确显示原因；研究与计划继续 Gate HOLD。
