---
id: family.news
title: 资讯
group: family
version: 1
applies_to: []
summary: "只存新闻标题与链接，不抓正文；发布时间未知时如实标注；免费来源不稳定。"
related: [family.sec_filings, agent.tool_calls, system.cutoff]
sources: [investment_assistant/data/adapters/news.py, investment_assistant/data/families.py, docs/features/daily-plan.md]
---

## 是什么

资讯是每只标的的新闻标题列表，只给 Agent 和你当背景参考。规则 v1 和 `plan-1` 都**不读**资讯，所以它不会影响候选和价位。

## 怎么算 / 从哪来

- 来源是 yfinance 的搜索接口（每只标的取最多 20 条），按相关标的归类。老的 `get_news` 接口已被 Yahoo 改成 404，所以改用搜索接口。
- 只存标题和链接，不抓正文。
- 发布时间解析不出来就记为未知，页面显示「发布时间未知」，不会拿采集时间代替发布时间。
- 「覆盖」的口径：近 7 天内有资讯标题的标的数。

## 在系统里哪里看到

- 「数据」菜单的「资讯」卡与浏览区（近 60 天，标题是可点的链接，带来源、发布者和发布时间）。
- 「标的」页的资讯列表；Agent 的 `get_news` 工具读的也是它（默认 7 天，最多 14 天，只限截止时间之前入库的）。

## 注意

- 免费来源不稳定：运行报告里 `news.fetched` 为 0，通常说明接口又失效了，而不是这几天没有新闻。
- 标题不是事实核验过的信息，也不是结论；Agent 引用资讯时同样要写明出处，见[工具调用](agent.tool_calls.md)。
